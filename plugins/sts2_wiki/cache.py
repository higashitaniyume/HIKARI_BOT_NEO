from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Sts2WikiResult

logger = logging.getLogger("HikariBot.Sts2Wiki.Cache")

CACHE_PATH = Path("UserData/sts2_wiki_cache.json")
_lock = threading.RLock()


class Sts2WikiCache:
    def __init__(
        self,
        *,
        path: Path = CACHE_PATH,
        ttl_seconds: int = 86400,
        max_entries: int = 500,
        namespace: str = "",
    ) -> None:
        self.path = path
        self.ttl_seconds = max(0, int(ttl_seconds))
        self.max_entries = max(10, int(max_entries))
        self.namespace = " ".join(namespace.strip().casefold().split())

    async def get(self, query: str) -> Sts2WikiResult | None:
        if self.ttl_seconds <= 0:
            return None
        return await asyncio.to_thread(self._get_sync, query)

    async def set(self, query: str, result: Sts2WikiResult) -> None:
        if self.ttl_seconds <= 0:
            return
        await asyncio.to_thread(self._set_sync, query, result)

    def _get_sync(self, query: str) -> Sts2WikiResult | None:
        key = _cache_key(query, self.namespace)
        if not key:
            return None
        with _lock:
            data = _read_cache(self.path)
            entries = _entries(data)
            item = entries.get(key)
            if not isinstance(item, dict) or _is_expired(item, self.ttl_seconds):
                return None
            result = _result_from_cache(item)
            if result is not None:
                result.cache_hit = True
            return result

    def _set_sync(self, query: str, result: Sts2WikiResult) -> None:
        key = _cache_key(query, self.namespace)
        if not key:
            return
        now = _utc_now()
        item = {
            "query": result.query or query,
            "title": result.title,
            "summary": result.summary,
            "extract": result.extract,
            "url": result.url,
            "updated_at": now,
            "namespace": self.namespace,
        }
        with _lock:
            data = _read_cache(self.path)
            entries = _entries(data)
            entries[key] = item
            data["version"] = 1
            data["entries"] = _prune_entries(entries, self.max_entries)
            _write_cache(self.path, data)
            result.updated_at = now


def _read_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("[Sts2Wiki] 缓存读取失败，将使用空缓存: %s", e)
        return {"version": 1, "entries": {}}
    return data if isinstance(data, dict) else {"version": 1, "entries": {}}


def _write_cache(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def _entries(data: dict[str, Any]) -> dict[str, Any]:
    entries = data.get("entries")
    if not isinstance(entries, dict):
        entries = {}
        data["entries"] = entries
    return entries


def _result_from_cache(item: dict[str, Any]) -> Sts2WikiResult | None:
    query = str(item.get("query") or "").strip()
    title = str(item.get("title") or "").strip()
    summary = str(item.get("summary") or "").strip()
    extract = str(item.get("extract") or "").strip()
    url = str(item.get("url") or "").strip()
    updated_at = str(item.get("updated_at") or "").strip()
    if not query or not title or not url:
        return None
    return Sts2WikiResult(
        query=query,
        title=title,
        summary=summary or extract,
        extract=extract or summary,
        url=url,
        updated_at=updated_at,
    )


def _prune_entries(entries: dict[str, Any], max_entries: int) -> dict[str, Any]:
    if len(entries) <= max_entries:
        return entries
    ordered = sorted(
        entries.items(),
        key=lambda item: str(item[1].get("updated_at") if isinstance(item[1], dict) else ""),
        reverse=True,
    )
    return dict(ordered[:max_entries])


def _is_expired(item: dict[str, Any], ttl_seconds: int) -> bool:
    updated_at = str(item.get("updated_at") or "").strip()
    if not updated_at:
        return True
    try:
        timestamp = datetime.fromisoformat(updated_at).timestamp()
    except ValueError:
        return True
    return datetime.now(timezone.utc).timestamp() - timestamp > ttl_seconds


def _cache_key(query: str, namespace: str = "") -> str:
    normalized_query = " ".join(query.strip().casefold().split())
    if not normalized_query:
        return ""
    normalized_namespace = " ".join(namespace.strip().casefold().split())
    if not normalized_namespace:
        return normalized_query
    return f"{normalized_namespace}::{normalized_query}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
