"""Persistent TTL cleanup for temporary media files."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("HikariBot.TempMediaCleaner")

DEFAULT_TEMP_MEDIA_TTL_SECONDS = 600
DEFAULT_CLEAN_INTERVAL_SECONDS = 60
REGISTRY_PATH = Path("UserData/temp_media_cleanup.json")
_MEDIA_PARSER_MARKER = ".astrbot_media_parser"
_registry_lock = threading.RLock()
_cleanup_task: asyncio.Task[None] | None = None
_driver_registered = False


@dataclass(slots=True)
class CleanupResult:
    deleted: int = 0
    missing: int = 0
    kept: int = 0
    errors: int = 0


def ttl_seconds_from_config(value: Any, default: int = DEFAULT_TEMP_MEDIA_TTL_SECONDS) -> int:
    """Parse a positive media cache TTL in seconds."""
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return max(1, int(default))


def register_temp_media_path(
    path: str | os.PathLike[str],
    *,
    ttl_seconds: int = DEFAULT_TEMP_MEDIA_TTL_SECONDS,
    kind: str | None = None,
    registry_path: Path = REGISTRY_PATH,
) -> None:
    """Register a temporary media file or marked directory for later cleanup."""
    resolved = Path(path).expanduser().resolve(strict=False)
    if not resolved.exists():
        return

    entry_kind = kind or ("dir" if resolved.is_dir() else "file")
    if entry_kind not in {"file", "dir"}:
        raise ValueError(f"unsupported temp media kind: {entry_kind}")
    if entry_kind == "dir" and not resolved.is_dir():
        return
    if entry_kind == "file" and not resolved.is_file():
        return

    now = time.time()
    entry = {
        "path": str(resolved),
        "kind": entry_kind,
        "expires_at": now + ttl_seconds_from_config(ttl_seconds),
        "updated_at": now,
    }

    with _registry_lock:
        data = _load_registry_unlocked(registry_path)
        entries = data.setdefault("entries", {})
        old = entries.get(str(resolved))
        if isinstance(old, dict) and old.get("created_at"):
            entry["created_at"] = old["created_at"]
        else:
            entry["created_at"] = now
        entries[str(resolved)] = entry
        _write_registry_unlocked(registry_path, data)


def cleanup_expired_temp_media(
    *,
    now: float | None = None,
    registry_path: Path = REGISTRY_PATH,
) -> CleanupResult:
    """Delete registered media paths whose TTL has expired."""
    current = time.time() if now is None else now
    result = CleanupResult()

    with _registry_lock:
        data = _load_registry_unlocked(registry_path)
        entries = data.get("entries", {})
        if not isinstance(entries, dict):
            entries = {}

        kept_entries: dict[str, dict[str, Any]] = {}
        for key, raw in entries.items():
            if not isinstance(raw, dict):
                continue
            expires_at = _as_float(raw.get("expires_at"), 0.0)
            if expires_at > current:
                kept_entries[key] = raw
                result.kept += 1
                continue

            path = Path(str(raw.get("path") or key)).expanduser().resolve(strict=False)
            kind = str(raw.get("kind") or "file")
            if not path.exists():
                result.missing += 1
                continue

            try:
                if _delete_registered_path(path, kind):
                    result.deleted += 1
                else:
                    result.errors += 1
                    kept_entries[key] = raw
            except Exception as e:
                logger.warning("[TempMedia] 清理过期媒体失败: path=%s error=%s", path, e)
                result.errors += 1
                kept_entries[key] = raw

        data["entries"] = kept_entries
        _write_registry_unlocked(registry_path, data)

    if result.deleted or result.missing or result.errors:
        logger.info(
            "[TempMedia] cleanup -> deleted=%d missing=%d kept=%d errors=%d",
            result.deleted,
            result.missing,
            result.kept,
            result.errors,
        )
    return result


def register_temp_media_cleaner(
    driver: Any,
    *,
    interval_seconds: int = DEFAULT_CLEAN_INTERVAL_SECONDS,
) -> None:
    """Register NoneBot lifecycle hooks for periodic temporary media cleanup."""
    global _driver_registered
    if _driver_registered:
        return
    _driver_registered = True

    @driver.on_startup
    async def _start_temp_media_cleaner() -> None:
        global _cleanup_task
        if _cleanup_task is not None and not _cleanup_task.done():
            return
        _cleanup_task = asyncio.create_task(
            _cleanup_loop(interval_seconds=max(1, int(interval_seconds))),
            name="HikariTempMediaCleaner",
        )
        logger.info(
            "[TempMedia] cleanup loop started interval=%ss default_ttl=%ss",
            max(1, int(interval_seconds)),
            DEFAULT_TEMP_MEDIA_TTL_SECONDS,
        )

    @driver.on_shutdown
    async def _stop_temp_media_cleaner() -> None:
        global _cleanup_task
        task = _cleanup_task
        _cleanup_task = None
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.to_thread(cleanup_expired_temp_media)


async def _cleanup_loop(*, interval_seconds: int) -> None:
    try:
        while True:
            await asyncio.to_thread(cleanup_expired_temp_media)
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("[TempMedia] cleanup loop stopped unexpectedly: %s", e)


def _delete_registered_path(path: Path, kind: str) -> bool:
    if kind == "dir":
        if not path.is_dir():
            return False
        if not (path / _MEDIA_PARSER_MARKER).is_file():
            logger.warning("[TempMedia] 拒绝清理未标记目录: %s", path)
            return False
        shutil.rmtree(path, ignore_errors=True)
        return True

    if not path.is_file():
        return False
    path.unlink()
    _remove_empty_marked_parent(path.parent)
    return True


def _remove_empty_marked_parent(parent: Path) -> None:
    marker = parent / _MEDIA_PARSER_MARKER
    if not marker.is_file():
        return
    try:
        remaining = list(parent.iterdir())
        if remaining == [marker]:
            marker.unlink(missing_ok=True)
            parent.rmdir()
    except OSError:
        return


def _load_registry_unlocked(registry_path: Path) -> dict[str, Any]:
    if not registry_path.exists():
        return {"version": 1, "entries": {}}
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("[TempMedia] 读取清理登记表失败，将重建: %s", e)
        return {"version": 1, "entries": {}}
    if not isinstance(data, dict):
        return {"version": 1, "entries": {}}
    if not isinstance(data.get("entries"), dict):
        data["entries"] = {}
    data["version"] = 1
    return data


def _write_registry_unlocked(registry_path: Path, data: dict[str, Any]) -> None:
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = registry_path.with_suffix(registry_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(registry_path)


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
