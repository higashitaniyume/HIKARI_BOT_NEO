"""Bandcamp 搜索 API 客户端 — 解析 HTML 搜索结果"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlparse, parse_qs

import httpx

from core.bot_identity import format_bot_name_text

logger = logging.getLogger("HikariBot.BandcampApi")

BANDCAMP_BASE = "https://bandcamp.com"
SEARCH_URL = f"{BANDCAMP_BASE}/search"


class BandcampError(RuntimeError):
    pass


class BandcampNotFound(BandcampError):
    pass


@dataclass(slots=True)
class BandcampResult:
    title: str
    url: str
    artist: str
    type: str  # "album", "track", "artist/label", "fan"
    release_date: str = ""
    thumbnail: str = ""


@dataclass(slots=True)
class BandcampSearchResults:
    query: str
    results: list[BandcampResult] = field(default_factory=list)


class BandcampClient:
    """Scrapes Bandcamp search results via httpx + HTML parsing.

    Bandcamp's search page returns server-rendered HTML which can be
    parsed reliably without an official API.  The structure is stable
    — ``li.searchresult`` items with known CSS classes.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.timeout = float(config.get("timeout") or 15)
        self.search_limit = max(1, min(int(config.get("search_limit") or 5), 10))
        self.proxy = str(config.get("proxy") or "").strip() or None
        self.user_agent = format_bot_name_text(
            config.get("user_agent") or "{bot_name} bandcamp_search"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _client_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(self.timeout, connect=min(self.timeout, 10.0)),
            "follow_redirects": True,
            "headers": {
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        }
        if self.proxy:
            kwargs["proxy"] = self.proxy
        return kwargs

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        type_filter: str | None = None,
        page: int = 1,
    ) -> BandcampSearchResults:
        keyword = query.strip()
        if not keyword:
            raise BandcampError("缺少搜索关键词")

        params: dict[str, str] = {"q": keyword, "page": str(page)}

        try:
            async with httpx.AsyncClient(**self._client_kwargs()) as client:
                resp = await client.get(SEARCH_URL, params=params)
                resp.raise_for_status()
                html_content = resp.text
        except httpx.RequestError as e:
            raise BandcampError(f"Bandcamp 连接失败: {type(e).__name__}") from e
        except httpx.HTTPStatusError as e:
            raise BandcampError(
                f"Bandcamp 请求失败: HTTP {e.response.status_code}"
            ) from e

        results = self._parse_results(html_content)

        if type_filter:
            results = [r for r in results if r.type == type_filter]

        results = results[: self.search_limit]

        if not results:
            raise BandcampNotFound(f"没有在 Bandcamp 找到「{keyword}」")

        return BandcampSearchResults(query=keyword, results=results)

    # ------------------------------------------------------------------
    # HTML Parsing
    # ------------------------------------------------------------------

    def _parse_results(self, html_content: str) -> list[BandcampResult]:
        """Parse Bandcamp search result HTML.

        Uses Python's built-in ``html.parser`` via BeautifulSoup so no
        C-dependency is required.  The CSS selectors mirror those used
        by SearXNG's Bandcamp engine:
        https://gitea.zaclys.com/zaclys/searxng/src/.../searx/engines/bandcamp.py
        """
        # Import locally so the dependency is only needed at call time
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            raise BandcampError(
                "缺少依赖 beautifulsoup4，请运行 uv sync 安装"
            ) from None

        soup = BeautifulSoup(html_content, "html.parser")
        items = soup.select("li.searchresult")
        results: list[BandcampResult] = []

        for item in items:
            try:
                result = self._parse_item(item)
                if result is not None:
                    results.append(result)
            except Exception as exc:
                logger.debug("解析 Bandcamp 搜索结果项失败: %s", exc)
                continue

        return results

    def _parse_item(self, item: Any) -> BandcampResult | None:
        """Parse a single ``li.searchresult`` element."""
        # ---- title & url ----
        heading_link = item.select_one(".heading a")
        if heading_link is None:
            return None
        title = heading_link.get_text(strip=True)
        url = str(heading_link.get("href") or "")
        if url.startswith("/"):
            url = BANDCAMP_BASE + url
        if not title or not url:
            return None

        # ---- artist / label (subhead) ----
        subhead = item.select_one(".subhead")
        artist = subhead.get_text(strip=True) if subhead is not None else ""

        # ---- item type ----
        type_elem = item.select_one(".itemtype")
        raw_type = type_elem.get_text(strip=True).lower() if type_elem is not None else ""
        type_map = {
            "album": "album",
            "track": "track",
            "artist": "artist/label",
            "label": "artist/label",
        }
        item_type = type_map.get(raw_type, raw_type)

        # ---- release date ----
        date_elem = item.select_one(".released")
        release_date = ""
        if date_elem is not None:
            date_text = date_elem.get_text(strip=True)
            date_text = re.sub(r"(?i)^released\s+", "", date_text).strip()
            if date_text:
                release_date = date_text

        # ---- thumbnail ----
        img = item.select_one(".art img")
        thumbnail = str(img.get("src", "")) if img is not None else ""

        return BandcampResult(
            title=title,
            url=url,
            artist=artist,
            type=item_type,
            release_date=release_date,
            thumbnail=thumbnail,
        )
