from __future__ import annotations

import re
from typing import Any

from .api import Sts2WikiClient
from .cache import Sts2WikiCache
from .models import Sts2WikiResult


class Sts2WikiKeywordEmpty(ValueError):
    pass


class Sts2WikiKeywordTooLong(ValueError):
    def __init__(self, max_chars: int) -> None:
        super().__init__(f"keyword exceeds {max_chars} chars")
        self.max_chars = max_chars


class Sts2WikiService:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.cache = Sts2WikiCache(
            ttl_seconds=int(config.get("cache_ttl_seconds") or 86400),
            max_entries=int(config.get("max_cache_entries") or 500),
        )
        self.client = Sts2WikiClient(config)

    async def lookup(self, keyword: str) -> Sts2WikiResult:
        cached = await self.cache.get(keyword)
        if cached is not None:
            return cached

        result = await self.client.search(keyword)
        await self.cache.set(keyword, result)
        return result


def normalize_keyword(value: str, *, max_chars: int = 80) -> str:
    keyword = re.sub(r"\s+", " ", value.strip())
    if not keyword:
        raise Sts2WikiKeywordEmpty()
    if len(keyword) > max_chars:
        raise Sts2WikiKeywordTooLong(max_chars)
    return keyword
