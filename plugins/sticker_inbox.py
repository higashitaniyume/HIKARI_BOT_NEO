from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from plugins import sticker_library

logger = logging.getLogger("HikariBot.StickerInbox")

INBOX_CONFIG_PATH = Path("BotData/plugin_configs/sticker_inbox.json")
INBOX_ROOT = Path("BotData/Gifs/_inbox")
MEDIA_EXTS = {".gif"}

_lock = threading.RLock()


def _empty_index() -> dict[str, Any]:
    return {"version": 1, "items": {}}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("[StickerInbox] 读取 JSON 失败，将忽略: %s -> %s", path, e)
        return {}


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _item_id_for_hash(content_hash: str) -> str:
    return f"{content_hash[:16]}.gif"


def _item_path(item_id: str) -> Path:
    return INBOX_ROOT / Path(item_id).name


def _normalize_index(index: dict[str, Any]) -> dict[str, Any]:
    normalized = _empty_index()
    normalized.update({k: v for k, v in index.items() if k != "items"})
    normalized["version"] = 1

    items: dict[str, Any] = {}
    for item_id, item in (index.get("items") or {}).items():
        item_id = Path(str(item_id)).name
        if not item_id:
            continue
        item = item if isinstance(item, dict) else {}
        file_name = Path(str(item.get("file") or item_id)).name
        if Path(file_name).suffix.lower() not in MEDIA_EXTS:
            continue
        items[item_id] = {
            "id": item_id,
            "file": file_name,
            "sha256": str(item.get("sha256") or "").strip(),
            "source": str(item.get("source") or "qq_message"),
            "sender_id": str(item.get("sender_id") or ""),
            "group_id": str(item.get("group_id") or ""),
            "message_id": str(item.get("message_id") or ""),
            "created_at": int(item.get("created_at") or time.time()),
            "original_name": str(item.get("original_name") or file_name),
        }

    normalized["items"] = items
    return normalized


def load_index() -> dict[str, Any]:
    with _lock:
        if INBOX_CONFIG_PATH.exists():
            return _normalize_index(_read_json(INBOX_CONFIG_PATH))
        index = _empty_index()
        save_index(index)
        return index


def save_index(index: dict[str, Any]) -> None:
    with _lock:
        normalized = _normalize_index(index)
        _atomic_write_json(INBOX_CONFIG_PATH, normalized)


def _formal_library_has_hash(content_hash: str) -> bool:
    library_index = sticker_library.load_index()
    for meta in (library_index.get("stickers") or {}).values():
        if str(meta.get("sha256") or "") == content_hash:
            return True
    return False


def _prune_to_limit(index: dict[str, Any], max_pending: int) -> int:
    if max_pending <= 0:
        max_pending = 1
    items = index.setdefault("items", {})
    over_count = len(items) - max_pending
    if over_count <= 0:
        return 0

    oldest = sorted(
        items.values(),
        key=lambda item: int(item.get("created_at") or 0),
    )[:over_count]
    removed = 0
    for item in oldest:
        item_id = str(item.get("id") or "")
        file_name = str(item.get("file") or item_id)
        if item_id in items:
            _item_path(file_name).unlink(missing_ok=True)
            items.pop(item_id, None)
            removed += 1
    return removed


def add_gif(
    gif_path: Path,
    *,
    metadata: dict[str, Any],
    max_pending: int,
) -> tuple[bool, str]:
    with _lock:
        if not gif_path.exists() or gif_path.stat().st_size <= 0:
            return False, "贴纸文件无效"

        content_hash = _hash_file(gif_path)
        item_id = _item_id_for_hash(content_hash)
        if _formal_library_has_hash(content_hash):
            return False, "正式贴纸库已存在"

        index = load_index()
        items = index.setdefault("items", {})
        if item_id in items:
            return False, "收集箱已存在"

        INBOX_ROOT.mkdir(parents=True, exist_ok=True)
        dest = _item_path(item_id)
        shutil.copy2(gif_path, dest)
        now = int(time.time())
        items[item_id] = {
            "id": item_id,
            "file": item_id,
            "sha256": content_hash,
            "source": str(metadata.get("source") or "qq_message"),
            "sender_id": str(metadata.get("sender_id") or ""),
            "group_id": str(metadata.get("group_id") or ""),
            "message_id": str(metadata.get("message_id") or ""),
            "created_at": int(metadata.get("created_at") or now),
            "original_name": str(metadata.get("original_name") or item_id),
        }
        removed = _prune_to_limit(index, max_pending)
        save_index(index)
        if removed:
            logger.info("[StickerInbox] 收集箱超过上限，已移除最旧项 %d 个", removed)
        return True, item_id


def list_items() -> list[dict[str, Any]]:
    index = load_index()
    items = list((index.get("items") or {}).values())
    items.sort(key=lambda item: int(item.get("created_at") or 0), reverse=True)
    return items


def get_item_path(item_id: str) -> Path | None:
    index = load_index()
    safe_id = Path(str(item_id or "")).name
    item = (index.get("items") or {}).get(safe_id)
    if not item:
        return None
    path = _item_path(str(item.get("file") or safe_id))
    if path.is_file() and path.suffix.lower() in MEDIA_EXTS and path.stat().st_size > 0:
        return path
    return None


def delete_items(item_ids: list[str]) -> int:
    with _lock:
        index = load_index()
        items = index.setdefault("items", {})
        removed = 0
        for raw_id in item_ids:
            item_id = Path(str(raw_id or "")).name
            item = items.pop(item_id, None)
            if not item:
                continue
            _item_path(str(item.get("file") or item_id)).unlink(missing_ok=True)
            removed += 1
        if removed:
            save_index(index)
        return removed


def assign_items(item_ids: list[str], pack_name: str, keywords: Any = "") -> dict[str, Any]:
    safe_pack = sticker_library.safe_pack_name(pack_name)
    if not safe_pack:
        raise ValueError("贴纸包名称不能为空。")

    with _lock:
        index = load_index()
        items = index.setdefault("items", {})
        paths: list[Path] = []
        assigned_ids: list[str] = []
        missing_ids: list[str] = []
        for raw_id in item_ids:
            item_id = Path(str(raw_id or "")).name
            item = items.get(item_id)
            if not item:
                missing_ids.append(item_id)
                continue
            path = _item_path(str(item.get("file") or item_id))
            if not path.exists() or path.stat().st_size <= 0:
                missing_ids.append(item_id)
                continue
            paths.append(path)
            assigned_ids.append(item_id)

        if not paths:
            return {"assigned": 0, "missing": missing_ids}

        sticker_library.register_pack_keywords(safe_pack, keywords, include_pack_name=True)
        sticker_library.save_gifs_to_pack(safe_pack, paths, source="inbox")
        for item_id in assigned_ids:
            item = items.pop(item_id, None)
            if item:
                _item_path(str(item.get("file") or item_id)).unlink(missing_ok=True)
        save_index(index)
        return {"assigned": len(assigned_ids), "missing": missing_ids}
