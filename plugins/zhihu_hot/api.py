from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

import httpx


class ZhihuHotError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ZhihuHotItem:
    rank: int
    title: str
    url: str
    heat: str = ""
    excerpt: str = ""
    answer_count: int = 0
    follower_count: int = 0
    question_id: int = 0
    trend: int = 0
    debut: bool = False

    @property
    def key(self) -> str:
        return f"zhihu:{self.question_id or self.url or self.title}"


_CACHE: dict[str, tuple[float, list[ZhihuHotItem]]] = {}


class ZhihuHotClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.api_url = str(config.get("api_url") or "").strip()
        self.timeout = float(config.get("timeout_seconds") or 20)
        self.proxy = str(config.get("proxy") or "").strip() or None
        self.cache_ttl = max(0, int(config.get("cache_ttl_minutes") or 5)) * 60
        if not self.api_url:
            raise ZhihuHotError("知乎热搜 API 地址未配置")

    def _client_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(self.timeout, connect=min(self.timeout, 10.0)),
            "follow_redirects": True,
            "headers": {
                "Accept": "application/json",
                "User-Agent": str(self.config.get("user_agent") or "HIKARI_BOT_NEO Zhihu Hot Reader"),
            },
        }
        if self.proxy:
            kwargs["proxy"] = self.proxy
        return kwargs

    async def fetch_hot_items(self, *, max_items: int | None = None, force_refresh: bool = False) -> list[ZhihuHotItem]:
        cache_key = f"{self.api_url}|{self.config.get('request_params')}|{self.config.get('user_agent')}"
        cached = _CACHE.get(cache_key)
        now = time.monotonic()
        if not force_refresh and cached and now - cached[0] < self.cache_ttl:
            return _limit_items(cached[1], max_items)

        request_params = self.config.get("request_params")
        params = dict(request_params) if isinstance(request_params, dict) else {}
        try:
            async with httpx.AsyncClient(**self._client_kwargs()) as client:
                response = await client.get(self.api_url, params=params)
                response.raise_for_status()
                data = response.json()
        except httpx.RequestError as e:
            raise ZhihuHotError(f"知乎热搜连接失败: {type(e).__name__}") from e
        except httpx.HTTPStatusError as e:
            raise ZhihuHotError(f"知乎热搜请求失败: HTTP {e.response.status_code}") from e
        except ValueError as e:
            raise ZhihuHotError("知乎热搜返回内容不是有效 JSON") from e

        items = parse_hot_list(data)
        _CACHE[cache_key] = (now, items)
        return _limit_items(items, max_items)


def parse_hot_list(data: Any, *, max_items: int | None = None) -> list[ZhihuHotItem]:
    if not isinstance(data, dict):
        raise ZhihuHotError("知乎热搜返回结构不是 JSON 对象")
    raw_items = data.get("data")
    if not isinstance(raw_items, list):
        raise ZhihuHotError("知乎热搜返回结构缺少 data 列表")

    items: list[ZhihuHotItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        target = raw.get("target")
        if not isinstance(target, dict):
            continue
        title = _clean_text(target.get("title"))
        if not title:
            continue
        question_id = _safe_int(target.get("id"))
        items.append(
            ZhihuHotItem(
                rank=len(items) + 1,
                title=title,
                url=_web_question_url(question_id, target.get("url")),
                heat=_clean_text(raw.get("detail_text")),
                excerpt=_clean_text(target.get("excerpt")),
                answer_count=_safe_int(target.get("answer_count")),
                follower_count=_safe_int(target.get("follower_count")),
                question_id=question_id,
                trend=_safe_int(raw.get("trend")),
                debut=bool(raw.get("debut")),
            )
        )
        if max_items is not None and len(items) >= max_items:
            break
    return items


def _limit_items(items: list[ZhihuHotItem], max_items: int | None) -> list[ZhihuHotItem]:
    if max_items is None:
        return list(items)
    return list(items[: max(1, max_items)])


def _web_question_url(question_id: int, value: Any) -> str:
    if question_id > 0:
        return f"https://www.zhihu.com/question/{question_id}"
    text = str(value or "").strip()
    match = re.search(r"/questions?/(\d+)", text)
    if match:
        return f"https://www.zhihu.com/question/{match.group(1)}"
    return text


def _clean_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0
