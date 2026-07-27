from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode, urlparse

import httpx

from .models import Sts2WikiCandidate, Sts2WikiResult
from .spire import (
    _compact_key,
    _endpoint_label,
    _search_categories,
    _spire_candidate,
    _spire_summary,
)
from .utils import (
    _clean_wikitext,
    _coerce_mw_text,
    _extract_intro_from_html,
    _first_paragraph,
    _normalize_text,
    _strip_html,
    _truncate,
)


class Sts2WikiError(RuntimeError):
    pass


class Sts2WikiNotFound(Sts2WikiError):
    pass


@dataclass(slots=True)
class _PageContent:
    title: str
    extract: str
    url: str


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


def _query_pages(data: dict[str, Any]) -> list[dict[str, Any]]:
    query = data.get("query") if isinstance(data.get("query"), dict) else {}
    pages = query.get("pages") if isinstance(query, dict) else None
    if isinstance(pages, list):
        return [page for page in pages if isinstance(page, dict)]
    if isinstance(pages, dict):
        return [page for page in pages.values() if isinstance(page, dict)]
    return []
