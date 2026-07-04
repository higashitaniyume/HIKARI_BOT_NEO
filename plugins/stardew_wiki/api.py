from __future__ import annotations

import asyncio
import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

import httpx

from core.bot_identity import bot_user_agent


class StardewWikiError(RuntimeError):
    pass


class StardewWikiNotFound(StardewWikiError):
    pass


@dataclass(slots=True)
class StardewWikiResult:
    title: str
    summary: str
    detail: str
    url: str
    image_url: str


class _IntroTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._paragraph_depth = 0
        self._current: list[str] = []
        self.paragraphs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "table"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "p":
            self._paragraph_depth += 1
            self._current = []

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            if tag in {"script", "style", "table"}:
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


class StardewWikiClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self.api_url = str(config.get("api_url") or "").strip()
        self.timeout = float(config.get("timeout") or 12)
        self.search_limit = max(1, min(int(config.get("search_limit") or 3), 10))
        self.summary_max_chars = max(60, int(config.get("summary_max_chars") or 220))
        self.detail_max_chars = max(self.summary_max_chars, int(config.get("detail_max_chars") or 1600))
        self.image_size = max(120, min(int(config.get("image_size") or 640), 1600))
        self.proxy = str(config.get("proxy") or "").strip() or None
        if not self.api_url:
            raise StardewWikiError("星露谷 Wiki API 地址未配置")

    def _client_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(self.timeout, connect=min(self.timeout, 10.0)),
            "follow_redirects": True,
            "headers": {
                "Accept": "application/json",
                "User-Agent": bot_user_agent("stardew_wiki"),
            },
        }
        if self.proxy:
            kwargs["proxy"] = self.proxy
        return kwargs

    async def search(self, query: str) -> StardewWikiResult:
        keyword = query.strip()
        if not keyword:
            raise StardewWikiError("缺少搜索关键词")

        page = await self._search_page(keyword)
        detail_result, image_result = await asyncio.gather(
            self._fetch_detail(page["title"]),
            self._fetch_main_image(page["title"]),
            return_exceptions=True,
        )
        if isinstance(detail_result, Exception):
            raise detail_result
        detail = detail_result or "这个页面暂时没有可提取的详细描述。"
        detail = _truncate(detail, self.detail_max_chars)
        summary = _truncate(_first_paragraph(detail), self.summary_max_chars)
        image_url = "" if isinstance(image_result, Exception) else image_result
        return StardewWikiResult(
            title=str(page["title"]),
            summary=summary,
            detail=detail,
            url=str(page["fullurl"]),
            image_url=image_url,
        )

    async def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(**self._client_kwargs()) as client:
                response = await client.get(self.api_url, params=params)
            response.raise_for_status()
            data = response.json()
        except httpx.RequestError as e:
            raise StardewWikiError(f"星露谷 Wiki 连接失败: {type(e).__name__}") from e
        except httpx.HTTPStatusError as e:
            raise StardewWikiError(f"星露谷 Wiki 请求失败: HTTP {e.response.status_code}") from e
        except ValueError as e:
            raise StardewWikiError("星露谷 Wiki 返回内容不是有效 JSON") from e
        if not isinstance(data, dict):
            raise StardewWikiError("星露谷 Wiki 返回格式异常")
        return data

    async def _search_page(self, keyword: str) -> dict[str, Any]:
        data = await self._request(
            {
                "action": "query",
                "generator": "search",
                "gsrsearch": keyword,
                "gsrlimit": self.search_limit,
                "prop": "info",
                "inprop": "url",
                "format": "json",
                "formatversion": 2,
            }
        )
        pages = data.get("query", {}).get("pages", [])
        if not isinstance(pages, list) or not pages:
            raise StardewWikiNotFound(f"没有找到「{keyword}」")
        pages.sort(key=lambda item: int(item.get("index") or 9999))
        page = pages[0]
        if not isinstance(page, dict) or not page.get("title") or not page.get("fullurl"):
            raise StardewWikiError("星露谷 Wiki 搜索结果格式异常")
        return page

    async def _fetch_detail(self, title: str) -> str:
        data = await self._request(
            {
                "action": "parse",
                "page": title,
                "prop": "text",
                "section": 0,
                "format": "json",
                "formatversion": 2,
            }
        )
        raw_html = data.get("parse", {}).get("text")
        if not isinstance(raw_html, str):
            return ""
        parser = _IntroTextParser()
        parser.feed(raw_html)
        return "\n\n".join(parser.paragraphs).strip()

    async def _fetch_main_image(self, title: str) -> str:
        data = await self._request(
            {
                "action": "query",
                "prop": "pageimages",
                "piprop": "thumbnail|original",
                "pithumbsize": self.image_size,
                "redirects": 1,
                "titles": title,
                "format": "json",
                "formatversion": 2,
            }
        )
        pages = data.get("query", {}).get("pages", [])
        if not isinstance(pages, list) or not pages:
            return ""
        page = pages[0]
        if not isinstance(page, dict):
            return ""
        return _image_source(page.get("original")) or _image_source(page.get("thumbnail"))


def _normalize_text(value: str) -> str:
    text = html.unescape(value)
    text = re.sub(r"\[\s*\d+\s*\]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _first_paragraph(value: str) -> str:
    return value.strip().split("\n", 1)[0].strip()


def _image_source(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    source = value.get("source")
    return source.strip() if isinstance(source, str) else ""


def _truncate(value: str, max_chars: int) -> str:
    text = value.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"
