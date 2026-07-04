from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urlencode, urlparse

import httpx

from .models import Sts2WikiCandidate, Sts2WikiResult


class Sts2WikiError(RuntimeError):
    pass


class Sts2WikiNotFound(Sts2WikiError):
    pass


@dataclass(slots=True)
class _PageContent:
    title: str
    extract: str
    url: str


class _IntroTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._paragraph_depth = 0
        self._current: list[str] = []
        self.paragraphs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "table", "nav"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "p":
            self._paragraph_depth += 1
            self._current = []

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            if tag in {"script", "style", "table", "nav"}:
                self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "p" and self._paragraph_depth:
            text = _normalize_text("".join(self._current))
            if text:
                self.paragraphs.append(text)
            self._paragraph_depth -= 1
            self._current = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not self._paragraph_depth:
            return
        self._current.append(data)


class Sts2WikiClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self.source = str(config.get("source") or "spire_codex").strip().casefold()
        self.api_url = str(config.get("api_url") or "").strip()
        self.site_url = str(config.get("site_url") or "").strip().rstrip("/")
        self.language = str(config.get("language") or "zhs").strip() or "zhs"
        self.version = str(config.get("version") or "").strip()
        self.timeout = float(config.get("timeout") or 10)
        self.search_limit = max(1, min(int(config.get("search_limit") or 5), 10))
        self.summary_max_chars = max(80, int(config.get("summary_max_chars") or 300))
        self.search_categories = _search_categories(config.get("search_categories"))
        self.proxy = str(config.get("proxy") or "").strip() or None
        self.user_agent = (
            str(config.get("user_agent") or "").strip()
            or "HikariBot/1.0 SlayTheSpire2WikiQuery"
        )
        if not self.api_url:
            raise Sts2WikiError("杀戮尖塔 2 Wiki API 地址未配置")

    def _client_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(self.timeout, connect=min(self.timeout, 10.0)),
            "follow_redirects": True,
            "headers": {
                "Accept": "application/json",
                "User-Agent": self.user_agent,
            },
        }
        if self.proxy:
            kwargs["proxy"] = self.proxy
        return kwargs

    async def search(self, query: str) -> Sts2WikiResult:
        if self.source in {"spire_codex", "spire-codex", "spirecodex"}:
            return await self._search_spire_codex(query)
        return await self._search_mediawiki(query)

    async def _search_spire_codex(self, query: str) -> Sts2WikiResult:
        keyword = query.strip()
        if not keyword:
            raise Sts2WikiError("缺少搜索关键词")

        candidates: list[_SpireCandidate] = []
        exact_candidate: _SpireCandidate | None = None
        for endpoint in self.search_categories:
            endpoint_candidates = await self._fetch_spire_candidates(endpoint, keyword)
            candidates.extend(endpoint_candidates)
            exact_candidate = next((candidate for candidate in endpoint_candidates if candidate.exact_name), None)
            if exact_candidate is not None:
                break

        if not candidates:
            raise Sts2WikiNotFound(f"没有找到「{keyword}」")

        best = exact_candidate or sorted(candidates, key=lambda item: item.score, reverse=True)[0]
        extract = _truncate(best.extract, max(self.summary_max_chars * 3, 900))
        summary = _truncate(_first_paragraph(extract), self.summary_max_chars)
        return Sts2WikiResult(
            query=keyword,
            title=f"{best.name}（{_endpoint_label(best.endpoint)}）",
            summary=summary,
            extract=extract,
            url=self._spire_page_url(best.endpoint, best.item_id),
            candidates=[
                Sts2WikiCandidate(title=f"{item.name}（{_endpoint_label(item.endpoint)}）", snippet=item.summary)
                for item in sorted(candidates, key=lambda item: item.score, reverse=True)[: self.search_limit]
            ],
        )

    async def _fetch_spire_candidates(self, endpoint: str, keyword: str) -> list["_SpireCandidate"]:
        params: dict[str, Any] = {
            "lang": self.language,
            "search": keyword,
        }
        if self.version:
            params["version"] = self.version

        data = await self._request_spire(endpoint, params)
        if not isinstance(data, list):
            return []

        query_key = _compact_key(keyword)
        candidates: list[_SpireCandidate] = []
        for index, item in enumerate(data):
            if not isinstance(item, dict):
                continue
            candidate = _spire_candidate(endpoint, item, query_key, index)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    async def _request_spire(self, endpoint: str, params: dict[str, Any]) -> Any:
        url = f"{self.api_url.rstrip('/')}/{endpoint.strip('/')}"
        try:
            async with httpx.AsyncClient(**self._client_kwargs()) as client:
                response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.RequestError as e:
            raise Sts2WikiError(f"杀戮尖塔 2 中文数据源连接失败: {type(e).__name__}") from e
        except httpx.HTTPStatusError as e:
            raise Sts2WikiError(f"杀戮尖塔 2 中文数据源请求失败: HTTP {e.response.status_code}") from e
        except ValueError as e:
            raise Sts2WikiError("杀戮尖塔 2 中文数据源返回内容不是有效 JSON") from e

    async def _search_mediawiki(self, query: str) -> Sts2WikiResult:
        keyword = query.strip()
        if not keyword:
            raise Sts2WikiError("缺少搜索关键词")

        candidates = await self.search_candidates(keyword)
        if not candidates:
            raise Sts2WikiNotFound(f"没有找到「{keyword}」")

        page = await self.fetch_page(candidates[0].title)
        extract = page.extract or "这个页面暂时没有可提取的摘要。"
        summary = _truncate(_first_paragraph(extract), self.summary_max_chars)
        return Sts2WikiResult(
            query=keyword,
            title=page.title or candidates[0].title,
            summary=summary,
            extract=extract,
            url=page.url or self._page_url(page.title or candidates[0].title),
            candidates=candidates,
        )

    async def search_candidates(self, keyword: str) -> list[Sts2WikiCandidate]:
        data = await self._request(
            {
                "action": "query",
                "list": "search",
                "srsearch": keyword,
                "srlimit": self.search_limit,
                "format": "json",
            }
        )
        query = data.get("query") if isinstance(data.get("query"), dict) else {}
        raw_results = query.get("search") if isinstance(query, dict) else None
        if not isinstance(raw_results, list):
            return []

        candidates: list[Sts2WikiCandidate] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            candidates.append(
                Sts2WikiCandidate(
                    title=title,
                    snippet=_normalize_text(_strip_html(str(item.get("snippet") or ""))),
                )
            )
        return candidates

    async def fetch_page(self, title: str) -> _PageContent:
        page = await self._fetch_extract_page(title)
        if page.extract:
            return page

        parsed = await self._fetch_parse_page(page.title or title)
        return _PageContent(
            title=parsed.title or page.title or title,
            extract=parsed.extract,
            url=page.url or parsed.url or self._page_url(parsed.title or page.title or title),
        )

    async def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(**self._client_kwargs()) as client:
                response = await client.get(self.api_url, params=params)
            response.raise_for_status()
            data = response.json()
        except httpx.RequestError as e:
            raise Sts2WikiError(f"杀戮尖塔 2 Wiki 连接失败: {type(e).__name__}") from e
        except httpx.HTTPStatusError as e:
            raise Sts2WikiError(f"杀戮尖塔 2 Wiki 请求失败: HTTP {e.response.status_code}") from e
        except ValueError as e:
            raise Sts2WikiError("杀戮尖塔 2 Wiki 返回内容不是有效 JSON") from e
        if not isinstance(data, dict):
            raise Sts2WikiError("杀戮尖塔 2 Wiki 返回格式异常")
        return data

    async def _fetch_extract_page(self, title: str) -> _PageContent:
        data = await self._request(
            {
                "action": "query",
                "prop": "extracts|info",
                "exintro": 1,
                "explaintext": 1,
                "inprop": "url",
                "redirects": 1,
                "titles": title,
                "format": "json",
                "formatversion": 2,
            }
        )
        pages = _query_pages(data)
        if not pages:
            return _PageContent(title=title, extract="", url="")
        page = pages[0]
        if "missing" in page:
            return _PageContent(title=title, extract="", url="")

        resolved_title = str(page.get("title") or title).strip()
        extract = page.get("extract")
        fullurl = page.get("fullurl") or page.get("canonicalurl")
        return _PageContent(
            title=resolved_title,
            extract=_normalize_text(extract) if isinstance(extract, str) else "",
            url=str(fullurl).strip() if isinstance(fullurl, str) else "",
        )

    async def _fetch_parse_page(self, title: str) -> _PageContent:
        data = await self._request(
            {
                "action": "parse",
                "page": title,
                "prop": "wikitext|text",
                "format": "json",
                "formatversion": 2,
            }
        )
        raw_parse = data.get("parse")
        parse = raw_parse if isinstance(raw_parse, dict) else {}
        resolved_title = str(parse.get("title") or title).strip()
        html_text = _coerce_mw_text(parse.get("text"))
        wikitext = _coerce_mw_text(parse.get("wikitext"))
        detail = _extract_intro_from_html(html_text) or _clean_wikitext(wikitext)
        return _PageContent(
            title=resolved_title,
            extract=_normalize_text(detail),
            url=self._page_url(resolved_title or title),
        )

    def _page_url(self, title: str) -> str:
        parsed = urlparse(self.api_url)
        if not parsed.scheme or not parsed.netloc:
            return ""
        slug = quote(title.strip().replace(" ", "_"), safe="/:_")
        return f"{parsed.scheme}://{parsed.netloc}/wiki/{slug}"

    def _spire_page_url(self, endpoint: str, item_id: str) -> str:
        base = self.site_url
        if not base:
            parsed = urlparse(self.api_url)
            base = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
        if not base:
            return ""
        language_prefix = f"/{self.language}" if self.language and self.language != "eng" else ""
        url = f"{base}{language_prefix}/{endpoint}/{quote(item_id, safe='')}"
        if self.version:
            url = f"{url}?{urlencode({'version': self.version})}"
        return url


@dataclass(slots=True)
class _SpireCandidate:
    endpoint: str
    item_id: str
    name: str
    summary: str
    extract: str
    exact_name: bool
    score: int


def _query_pages(data: dict[str, Any]) -> list[dict[str, Any]]:
    query = data.get("query") if isinstance(data.get("query"), dict) else {}
    pages = query.get("pages") if isinstance(query, dict) else None
    if isinstance(pages, list):
        return [page for page in pages if isinstance(page, dict)]
    if isinstance(pages, dict):
        return [page for page in pages.values() if isinstance(page, dict)]
    return []


_DEFAULT_SEARCH_CATEGORIES = (
    "cards",
    "characters",
    "relics",
    "potions",
    "powers",
    "keywords",
    "monsters",
    "events",
)

_ENDPOINT_LABELS = {
    "cards": "卡牌",
    "characters": "角色",
    "relics": "遗物",
    "potions": "药水",
    "powers": "能力效果",
    "keywords": "关键词",
    "monsters": "怪物",
    "events": "事件",
    "encounters": "遭遇",
    "acts": "章节",
    "ascensions": "进阶",
    "orbs": "充能球",
    "afflictions": "苦痛",
    "modifiers": "修正",
    "achievements": "成就",
}

_CHARACTER_LABELS = {
    "ironclad": "铁甲战士",
    "silent": "静默猎手",
    "defect": "故障机器人",
    "regent": "储君",
    "necrobinder": "亡灵契约师",
    "shared": "通用",
    "colorless": "无色",
    "token": "衍生",
}


def _search_categories(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return _DEFAULT_SEARCH_CATEGORIES
    categories = [str(item).strip() for item in value if str(item).strip()]
    return tuple(categories) or _DEFAULT_SEARCH_CATEGORIES


def _endpoint_label(endpoint: str) -> str:
    return _ENDPOINT_LABELS.get(endpoint, endpoint)


def _spire_candidate(endpoint: str, item: dict[str, Any], query_key: str, index: int) -> _SpireCandidate | None:
    item_id = str(item.get("id") or "").strip()
    name = str(item.get("name") or "").strip()
    if not item_id or not name:
        return None

    fields = _spire_text_fields(item)
    haystack = _compact_key(" ".join([name, *fields]))
    name_key = _compact_key(name)
    exact_name = bool(query_key and name_key == query_key)
    if query_key and query_key not in haystack:
        return None

    summary = _spire_summary(endpoint, item)
    extract = _spire_extract(endpoint, item)
    score = _spire_score(endpoint, name_key, haystack, query_key, index)
    return _SpireCandidate(
        endpoint=endpoint,
        item_id=item_id,
        name=name,
        summary=summary,
        extract=extract,
        exact_name=exact_name,
        score=score,
    )


def _spire_text_fields(item: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    for key in ("description", "flavor", "type", "rarity", "pool", "color"):
        value = item.get(key)
        if isinstance(value, str):
            fields.append(value)
    tags = item.get("tags")
    if isinstance(tags, list):
        fields.extend(str(tag) for tag in tags)
    return fields


def _spire_score(endpoint: str, name_key: str, haystack: str, query_key: str, index: int) -> int:
    endpoint_rank = list(_DEFAULT_SEARCH_CATEGORIES).index(endpoint) if endpoint in _DEFAULT_SEARCH_CATEGORIES else 99
    score = 1000 - endpoint_rank * 20 - index
    if query_key and name_key == query_key:
        score += 10000
    elif query_key and name_key.startswith(query_key):
        score += 3000
    elif query_key and query_key in name_key:
        score += 1500
    elif query_key and query_key in haystack:
        score += 100
    return score


def _spire_summary(endpoint: str, item: dict[str, Any]) -> str:
    parts = [_endpoint_label(endpoint)]
    if endpoint == "cards":
        parts.extend(
            part
            for part in (
                _character_label(item.get("color")),
                _safe_text(item.get("type")),
                _safe_text(item.get("rarity")),
                _cost_label(item),
            )
            if part
        )
    elif endpoint in {"relics", "potions"}:
        parts.extend(part for part in (_character_label(item.get("pool")), _safe_text(item.get("rarity"))) if part)
    elif endpoint == "characters":
        parts.extend(
            part
            for part in (
                f"生命 {item.get('starting_hp')}" if item.get("starting_hp") is not None else "",
                f"初始金币 {item.get('starting_gold')}" if item.get("starting_gold") is not None else "",
                f"能量 {item.get('max_energy')}" if item.get("max_energy") is not None else "",
            )
            if part
        )
    elif endpoint == "monsters":
        parts.append(_safe_text(item.get("type")))
    return " · ".join(part for part in parts if part)


def _spire_extract(endpoint: str, item: dict[str, Any]) -> str:
    lines = [_spire_summary(endpoint, item)]
    description = _strip_spire_markup(_safe_text(item.get("description")))
    if description:
        lines.append(description)

    if endpoint == "cards":
        upgrade = _strip_spire_markup(_safe_text(item.get("upgrade_description")))
        if upgrade and upgrade != description:
            lines.append(f"升级：{upgrade}")
    flavor = _strip_spire_markup(_safe_text(item.get("flavor")))
    if flavor:
        lines.append(f"描述：{flavor}")
    return "\n".join(line for line in lines if line)


def _safe_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _character_label(value: Any) -> str:
    key = str(value or "").strip().casefold()
    return _CHARACTER_LABELS.get(key, str(value).strip() if value else "")


def _cost_label(item: dict[str, Any]) -> str:
    if item.get("is_x_cost"):
        return "费用 X"
    if item.get("is_x_star_cost"):
        return "星能 X"
    star_cost = item.get("star_cost")
    if star_cost is not None:
        return f"星能 {star_cost}"
    cost = item.get("cost")
    if cost is None:
        return ""
    return f"费用 {cost}"


def _strip_spire_markup(value: str) -> str:
    text = value
    text = re.sub(r"\[energy:(\d+)\]", r"\1费", text)
    text = re.sub(r"\[star:(\d+)\]", r"\1星", text)
    text = re.sub(r"\[/?[a-z]+(?:[:=][^\]]+)?\]", "", text, flags=re.IGNORECASE)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _compact_key(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().casefold())


def _coerce_mw_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        raw = value.get("*")
        return raw if isinstance(raw, str) else ""
    return ""


def _extract_intro_from_html(value: str) -> str:
    if not value:
        return ""
    parser = _IntroTextParser()
    parser.feed(value)
    return "\n\n".join(parser.paragraphs).strip()


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", value)


def _clean_wikitext(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    for _ in range(6):
        updated = re.sub(r"\{\{[^{}]*\}\}", " ", text, flags=re.DOTALL)
        if updated == text:
            break
        text = updated
    text = re.sub(r"'''?", "", text)
    text = re.sub(r"\[\[(?:[^|\]]+\|)?([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[https?://[^\s\]]+\s+([^\]]+)\]", r"\1", text)
    text = re.sub(r"={2,}[^=\n]+={2,}.*", "", text, flags=re.DOTALL)
    return _normalize_text(text)


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = html.unescape(value)
    text = re.sub(r"\[\s*\d+\s*\]", "", text)
    paragraphs = re.split(r"(?:\r?\n){2,}", text)
    lines: list[str] = []
    for paragraph in paragraphs:
        line = re.sub(r"\s+", " ", paragraph).strip()
        if line:
            lines.append(line)
    return "\n\n".join(lines)


def _first_paragraph(value: str) -> str:
    return value.strip().split("\n", 1)[0].strip()


def _truncate(value: str, max_chars: int) -> str:
    text = value.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"
