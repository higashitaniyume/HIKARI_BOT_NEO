"""Bandcamp 搜索 API 客户端 — 通过 SearXNG 查询 + 直接页面抓取 + 网易云跨引用"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from core.bot_identity import format_bot_name_text

logger = logging.getLogger("HikariBot.BandcampApi")

BANDCAMP_BASE = "https://bandcamp.com"


class BandcampError(RuntimeError):
    pass


class BandcampNotFound(BandcampError):
    pass


# -- data models --------------------------------------------------------------


@dataclass(slots=True)
class BandcampResult:
    title: str
    url: str
    artist: str = ""
    type: str = "album"  # "album", "track", "artist/label"
    description: str = ""
    thumbnail: str = ""
    netease_url: str = ""  # cross-reference to NetEase Cloud Music


@dataclass(slots=True)
class BandcampSearchResults:
    query: str
    results: list[BandcampResult] = field(default_factory=list)


# -- helpers ------------------------------------------------------------------


def _infer_type(url: str) -> str:
    """Determine result type from a Bandcamp URL pattern."""
    path = urlparse(url).path.rstrip("/")
    if re.search(r"/album/", path):
        return "album"
    if re.search(r"/track/", path):
        return "track"
    if re.search(r"/merch/", path):
        return "artist/label"
    # Root of subdomain → artist/label page
    if path in ("", "/music", "/feed"):
        return "artist/label"
    return "album"  # default


def _parse_bandcamp_title(page_title: str) -> tuple[str, str]:
    """Split a Bandcamp page title into (title, artist).

    Typical formats::

        "Album Title | Artist Name"
        "Track Title | Artist Name"
        "Music | Artist Name"          → artist/label root
        "Artist Name"                  → fallback
    """
    if " | " in page_title:
        parts = page_title.split(" | ", 1)
        if parts[0].strip().lower() == "music":
            # "Music | Taishi" → artist page
            return parts[1].strip(), ""
        return parts[0].strip(), parts[1].strip()
    return page_title, ""


def _searxng_endpoint(base_url: Any) -> str:
    """Normalise the SearXNG base URL to a full /search endpoint."""
    base = str(base_url or "http://searxng-core:8080").strip().rstrip("/")
    if not base:
        base = "http://searxng-core:8080"
    if base.endswith("/search"):
        return base
    return f"{base}/search"


async def _fetch_page_metadata(url: str, timeout: float, proxy: str | None = None) -> dict[str, Any] | None:
    """Fetch an individual Bandcamp page and extract JSON-LD / data-* metadata.

    Works only for pages that are *not* behind the JS challenge
    (e.g. ``artist.bandcamp.com``, ``artist.bandcamp.com/album/...``).
    Returns a dict with keys ``title``, ``url``, ``thumbnail`` or ``None``.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    kwargs: dict[str, Any] = {
        "timeout": httpx.Timeout(timeout, connect=min(timeout, 10.0)),
        "follow_redirects": True,
        "headers": headers,
    }
    if proxy:
        kwargs["proxy"] = proxy

    try:
        async with httpx.AsyncClient(**kwargs) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            text = resp.text
    except httpx.RequestError:
        return None
    except httpx.HTTPStatusError:
        return None

    # Extract <title>
    title_match = re.search(r"<title>([^<]+)</title>", text)
    if not title_match:
        return None
    page_title = title_match.group(1).strip()

    # Extract JSON-LD (schema.org) for detailed metadata
    description = ""
    thumbnail = ""
    for match in re.finditer(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        text,
        re.DOTALL,
    ):
        try:
            import json

            data = json.loads(match.group(1))
            if isinstance(data, dict):
                desc = data.get("description") or ""
                if isinstance(desc, str) and desc.strip():
                    description = desc.strip()
                img = data.get("image") or ""
                if isinstance(img, str) and img.startswith("http"):
                    thumbnail = img
        except (json.JSONDecodeError, AttributeError):
            continue
        if thumbnail:
            break

    # Fallback: try data-band attribute for artist info
    if not description or not thumbnail:
        band_match = re.search(r'data-band="([^"]+)"', text)
        if band_match:
            try:
                import json

                band_data = json.loads(band_match.group(1).replace("&quot;", '"'))
                if not description:
                    description = band_data.get("bio", "")
            except (json.JSONDecodeError, AttributeError):
                pass

    return {
        "title": page_title,
        "url": url,
        "description": description,
        "thumbnail": thumbnail,
    }


# -- NetEase cross-reference --------------------------------------------------


NETEASE_BASE = "https://music.163.com"


def _netease_search_type(bc_type: str) -> int:
    """Map Bandcamp result type to NetEase API search type."""
    return {"album": 10, "track": 1, "artist/label": 100}.get(bc_type, 1)


def _netease_url(item_type: str, item_id: int) -> str:
    """Build a music.163.com URL from type and ID."""
    segments = {"album": "album", "track": "song", "artist/label": "artist"}
    seg = segments.get(item_type, "song")
    return f"{NETEASE_BASE}/#/{seg}?id={item_id}"


async def _search_netease(
    api_base: str,
    query: str,
    search_type: int,
    timeout: float,
) -> dict[str, Any] | None:
    """Search NetEase Cloud Music via the self-hosted API.

    Returns the first result item dict, or ``None`` on failure / no results.
    """
    url = f"{api_base.rstrip('/')}/cloudsearch"
    params = {"keywords": query, "type": search_type, "limit": 1}

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(timeout, 5.0))
        ) as client:
            resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.debug("[Bandcamp] NetEase 搜索失败 query=%r error=%s", query, exc)
        return None

    if data.get("code") != 200:
        return None

    result = data.get("result")
    if not isinstance(result, dict):
        return None

    # The key depends on search type
    key_map = {10: "albums", 1: "songs", 100: "artists"}
    items = result.get(key_map.get(search_type, "songs"), [])
    if not items:
        return None

    return items[0]


def _is_name_match(bc_title: str, bc_artist: str, netease_item: dict[str, Any], search_type: int) -> bool:
    """Check if a NetEase search result is likely the same item as the Bandcamp one.

    Uses a simple heuristic: the NetEase title must contain the Bandcamp title
    (or vice versa), and if the artist is known, at least one artist name should overlap.
    """
    # Normalise for comparison
    def norm(s: str) -> str:
        return re.sub(r"\s+", "", s.lower())

    bc_norm = norm(bc_title)

    if search_type == 10:  # album
        ne_title = norm(netease_item.get("name", ""))
        if bc_norm not in ne_title and ne_title not in bc_norm:
            return False
        # Check artist if available
        ne_artist = norm(netease_item.get("artist", {}).get("name", ""))
        if bc_artist and ne_artist:
            bc_artist_norm = norm(bc_artist)
            if bc_artist_norm not in ne_artist and ne_artist not in bc_artist_norm:
                return False
        return True

    elif search_type == 1:  # song
        ne_title = norm(netease_item.get("name", ""))
        if bc_norm not in ne_title and ne_title not in bc_norm:
            return False
        # Check at least one artist matches
        if bc_artist:
            bc_artist_norm = norm(bc_artist)
            ne_artists = [norm(a.get("name", "")) for a in (netease_item.get("ar") or [])]
            if ne_artists and not any(
                bc_artist_norm in a or a in bc_artist_norm for a in ne_artists
            ):
                return False
        return True

    elif search_type == 100:  # artist
        ne_name = norm(netease_item.get("name", ""))
        # For artists, also check aliases
        ne_aliases = [norm(a) for a in (netease_item.get("alias") or [])]
        return bc_norm in ne_name or ne_name in bc_norm or any(bc_norm in a or a in bc_norm for a in ne_aliases)

    return True


# -- client -------------------------------------------------------------------


class BandcampClient:
    """Search Bandcamp via SearXNG's general web search, falling back to
    direct page scraping for known-URL lookups.

    Bandcamp's own search page is behind a JavaScript challenge that
    blocks plain HTTP clients, so we rely on SearXNG (which is part of
    the project's Docker stack) to find Bandcamp content via
    ``site:bandcamp.com`` queries.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.search_limit = max(1, min(int(config.get("search_limit") or 5), 10))
        self.timeout = float(config.get("timeout") or 15)
        self.searxng_url = str(config.get("searxng_url") or "http://searxng-core:8080").strip()
        self.proxy = str(config.get("proxy") or "").strip() or None
        self.user_agent = format_bot_name_text(
            config.get("user_agent") or "{bot_name} bandcamp_search"
        )
        self.netease_api_url = str(config.get("netease_api_url") or "").strip() or ""
        self.cross_reference = bool(config.get("cross_reference", True))

    # -- search via SearXNG ---------------------------------------------------

    async def search(
        self,
        query: str,
        type_filter: str | None = None,
    ) -> BandcampSearchResults:
        keyword = query.strip()
        if not keyword:
            raise BandcampError("缺少搜索关键词")

        url = _searxng_endpoint(self.searxng_url)

        # Build a SearXNG-friendly query
        searxng_q = f"site:bandcamp.com {keyword}"

        params: dict[str, Any] = {
            "q": searxng_q,
            "format": "json",
            "safesearch": 1,
            "categories": "general",
        }

        kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(self.timeout, connect=min(self.timeout, 10.0)),
        }
        if self.proxy:
            kwargs["proxy"] = self.proxy

        try:
            async with httpx.AsyncClient(**kwargs) as client:
                resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        except httpx.RequestError as e:
            raise BandcampError(f"SearXNG 连接失败: {type(e).__name__}") from e
        except httpx.HTTPStatusError as e:
            raise BandcampError(
                f"SearXNG 请求失败: HTTP {e.response.status_code}"
            ) from e
        except (ValueError, KeyError) as e:
            raise BandcampError(f"SearXNG 返回格式异常: {e}") from e

        raw_results = data.get("results") if isinstance(data, dict) else []
        parsed: list[BandcampResult] = []

        if isinstance(raw_results, list):
            for item in raw_results:
                if not isinstance(item, dict):
                    continue
                url_str = str(item.get("url") or "").strip()
                if not url_str or "bandcamp.com" not in url_str:
                    continue

                title_raw = str(item.get("title") or "").strip()
                snippet = str(item.get("content") or "").strip()

                bc_type = _infer_type(url_str)

                if type_filter and bc_type != type_filter:
                    continue

                # Bandcamp titles are usually "Content | Artist"
                bc_title, artist = _parse_bandcamp_title(title_raw)

                parsed.append(
                    BandcampResult(
                        title=bc_title or title_raw,
                        url=url_str,
                        artist=artist,
                        type=bc_type,
                        description=snippet,
                    )
                )

        parsed = parsed[: self.search_limit]

        if not parsed:
            raise BandcampNotFound(f"没有在 Bandcamp 找到「{keyword}」")

        results = BandcampSearchResults(query=keyword, results=parsed)

        # Cross-reference to NetEase Cloud Music
        if self.cross_reference and self.netease_api_url:
            await self._cross_reference_all(results)

        return results

    # -- direct page metadata -------------------------------------------------

    async def lookup_page(self, bandcamp_url: str) -> BandcampResult | None:
        """Fetch metadata from a known Bandcamp page URL directly.

        Useful when the user provides a partial URL such as
        ``taishi/compllege`` → ``https://taishi.bandcamp.com/compllege``.
        """
        if not bandcamp_url.startswith("http"):
            # partial path — build https://<subdomain>.bandcamp.com/<path>
            if "/" in bandcamp_url:
                sub, path = bandcamp_url.split("/", 1)
                bandcamp_url = f"https://{sub}.bandcamp.com/{path}"
            else:
                bandcamp_url = f"https://{bandcamp_url}.bandcamp.com"

        meta = await _fetch_page_metadata(bandcamp_url, self.timeout, self.proxy)
        if meta is None:
            return None

        bc_title, artist = _parse_bandcamp_title(meta["title"])
        bc_type = _infer_type(bandcamp_url)
        result = BandcampResult(
            title=bc_title or meta["title"],
            url=meta["url"],
            artist=artist,
            type=bc_type,
            description=meta.get("description", ""),
            thumbnail=meta.get("thumbnail", ""),
        )

        # Cross-reference to NetEase
        if self.cross_reference and self.netease_api_url:
            await self._cross_reference_one(result)

        return result

    # -- NetEase cross-referencing --------------------------------------------

    async def _cross_reference_all(self, results: BandcampSearchResults) -> None:
        """Search NetEase for each result in parallel."""
        if not self.netease_api_url or not results.results:
            return

        coros = [self._cross_reference_one(r) for r in results.results]
        await asyncio.gather(*coros, return_exceptions=True)

    async def _cross_reference_one(self, result: BandcampResult) -> None:
        """Search NetEase for a single result and attach a link if matched."""
        if not self.netease_api_url:
            return

        search_type = _netease_search_type(result.type)

        # Build a search query
        if result.type == "artist/label":
            query = result.title
        else:
            query = f"{result.title} {result.artist}".strip()

        if not query:
            return

        item = await _search_netease(
            self.netease_api_url, query, search_type, self.timeout
        )
        if item is None:
            return

        # Verify the match looks reasonable
        if not _is_name_match(result.title, result.artist, item, search_type):
            return

        item_id = item.get("id")
        if not item_id:
            return

        result.netease_url = _netease_url(result.type, int(item_id))


# Import asyncio for gather used in cross-referencing
import asyncio  # noqa: E402
