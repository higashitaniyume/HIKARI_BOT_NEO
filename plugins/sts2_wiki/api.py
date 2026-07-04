from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urlparse

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
        self.api_url = str(config.get("api_url") or "").strip()
        self.timeout = float(config.get("timeout") or 10)
        self.search_limit = max(1, min(int(config.get("search_limit") or 5), 10))
        self.summary_max_chars = max(80, int(config.get("summary_max_chars") or 300))
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


def _query_pages(data: dict[str, Any]) -> list[dict[str, Any]]:
    query = data.get("query") if isinstance(data.get("query"), dict) else {}
    pages = query.get("pages") if isinstance(query, dict) else None
    if isinstance(pages, list):
        return [page for page in pages if isinstance(page, dict)]
    if isinstance(pages, dict):
        return [page for page in pages.values() if isinstance(page, dict)]
    return []


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
