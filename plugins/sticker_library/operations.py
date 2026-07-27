"""
贴纸库操作模块。

提供贴纸包的增删改查和贴纸文件的批量操作。
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from .index import (
    MEDIA_EXTS,
    PACK_PREVIEW_LIMIT,
    STORAGE_ROOT,
    _add_file_to_index,
    _add_keywords_to_index,
    _ensure_pack,
    _file_path_from_meta,
    _pack_files_from_index,
    _storage_path,
    load_index,
    safe_pack_name,
    save_index,
    split_keywords,
)

logger = logging.getLogger("HikariBot.StickerLibrary")
_lock = threading.RLock()


def list_pack_names() -> list[str]:
    index = load_index()
    return sorted((index.get("packs") or {}).keys())


def get_pack_files(pack_name: str) -> list[Path]:
    index = load_index()
    return _pack_files_from_index(index, pack_name, check_exists=True)


def get_sticker_path(sticker_id: str) -> Path | None:
    index = load_index()
    safe_id = Path(str(sticker_id or "")).name
    if not safe_id:
        return None
    stickers = index.get("stickers") or {}
    if safe_id not in stickers:
        referenced = any(
            safe_id == Path(str(pack_sticker_id)).name
            for pack in (index.get("packs") or {}).values()
            for pack_sticker_id in pack.get("stickers") or []
        )
        if not referenced:
            return None
    path = _file_path_from_meta(stickers, safe_id)
    if path.is_file() and path.suffix.lower() in MEDIA_EXTS and path.stat().st_size > 0:
        return path
    return None


def get_packs_files(pack_names: list[str]) -> list[Path]:
    index = load_index()
    files: list[Path] = []
    seen: set[Path] = set()
    for pack_name in pack_names:
        for path in _pack_files_from_index(index, pack_name, check_exists=True):
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                files.append(path)
    return files


def get_all_files() -> list[Path]:
    index = load_index()
    return get_packs_files(sorted((index.get("packs") or {}).keys()))


def count_pack(pack_name: str) -> int:
    index = load_index()
    pack = (index.get("packs") or {}).get(safe_pack_name(pack_name))
    return len(pack.get("stickers") or []) if pack else 0


def _sticker_detail(stickers: dict[str, Any], sticker_id: str) -> dict[str, Any]:
    meta = stickers.get(sticker_id) or {}
    path = _file_path_from_meta(stickers, sticker_id)
    exists = path.is_file() and path.suffix.lower() in MEDIA_EXTS and path.stat().st_size > 0
    return {
        "id": sticker_id,
        "file": Path(str(meta.get("file") or sticker_id)).name,
        "original_name": str(meta.get("original_name") or sticker_id),
        "source": str(meta.get("source") or "unknown"),
        "created_at": int(meta.get("created_at") or 0),
        "size": path.stat().st_size if exists else 0,
        "missing": not exists,
    }


def get_pack_detail(pack_name: str) -> dict[str, Any] | None:
    safe_name = safe_pack_name(pack_name)
    if not safe_name:
        raise ValueError("贴纸包名称不能为空。")

    index = load_index()
    pack = (index.get("packs") or {}).get(safe_name)
    if not pack:
        return None

    stickers = index.get("stickers") or {}
    sticker_ids: list[str] = []
    for sticker_id in pack.get("stickers") or []:
        safe_id = Path(str(sticker_id)).name
        if safe_id and safe_id not in sticker_ids:
            sticker_ids.append(safe_id)
    return {
        "name": safe_name,
        "count": len(sticker_ids),
        "keywords": split_keywords(pack.get("keywords") or []),
        "stickers": [_sticker_detail(stickers, sticker_id) for sticker_id in sticker_ids],
    }


def get_pack_archive_files(pack_name: str) -> tuple[str, list[tuple[Path, str]]]:
    import re

    detail = get_pack_detail(pack_name)
    if detail is None:
        raise ValueError("没有找到这个贴纸包。")

    index = load_index()
    stickers = index.get("stickers") or {}
    archive_files: list[tuple[Path, str]] = []
    used_names: set[str] = set()
    for position, sticker in enumerate(detail["stickers"], start=1):
        sticker_id = sticker["id"]
        path = _file_path_from_meta(stickers, sticker_id)
        if not path.is_file() or path.stat().st_size <= 0:
            continue
        base_name = Path(str(sticker.get("original_name") or path.name)).name
        base_name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", base_name).strip(" ._") or path.name
        if Path(base_name).suffix.lower() != ".gif":
            base_name = f"{base_name}.gif"
        archive_name = f"{position:03d}_{base_name}"
        while archive_name.casefold() in used_names:
            archive_name = f"{position:03d}_{sticker_id}"
        used_names.add(archive_name.casefold())
        archive_files.append((path, archive_name))
    return detail["name"], archive_files


def remove_stickers_from_pack(pack_name: str, sticker_ids: list[str]) -> dict[str, Any]:
    safe_name = safe_pack_name(pack_name)
    if not safe_name:
        raise ValueError("贴纸包名称不能为空。")
    target_ids = [
        Path(str(sticker_id)).name
        for sticker_id in sticker_ids
        if Path(str(sticker_id)).name
    ]
    if not target_ids:
        raise ValueError("请选择要删除的贴纸。")

    with _lock:
        index = load_index()
        packs = index.setdefault("packs", {})
        pack = packs.get(safe_name)
        if not pack:
            raise ValueError("没有找到这个贴纸包。")

        target_set = set(target_ids)
        current_ids = [
            Path(str(sticker_id)).name
            for sticker_id in pack.get("stickers") or []
            if Path(str(sticker_id)).name
        ]
        next_ids = [sticker_id for sticker_id in current_ids if sticker_id not in target_set]
        removed_ids = [sticker_id for sticker_id in current_ids if sticker_id in target_set]
        if not removed_ids:
            return {"pack": safe_name, "removed": 0, "deleted_files": 0}

        pack["stickers"] = next_ids
        still_referenced: set[str] = set()
        for other_pack in packs.values():
            for sticker_id in other_pack.get("stickers") or []:
                safe_id = Path(str(sticker_id)).name
                if safe_id:
                    still_referenced.add(safe_id)

        stickers = index.setdefault("stickers", {})
        deleted_files = 0
        for sticker_id in removed_ids:
            if sticker_id in still_referenced:
                continue
            path = _file_path_from_meta(stickers, sticker_id)
            stickers.pop(sticker_id, None)
            try:
                if path.is_file():
                    path.unlink()
                    deleted_files += 1
            except Exception as e:
                logger.warning("[StickerLibrary] 删除贴纸文件失败: %s -> %s", path, e)

        save_index(index)
        return {"pack": safe_name, "removed": len(removed_ids), "deleted_files": deleted_files}


def move_stickers_between_packs(source_pack: str, target_pack: str, sticker_ids: list[str]) -> dict[str, Any]:
    safe_source = safe_pack_name(source_pack)
    safe_target = safe_pack_name(target_pack)
    if not safe_source or not safe_target:
        raise ValueError("来源和目标贴纸包都不能为空。")
    if safe_source == safe_target:
        raise ValueError("目标贴纸包不能和当前贴纸包相同。")
    target_ids = [
        Path(str(sticker_id)).name
        for sticker_id in sticker_ids
        if Path(str(sticker_id)).name
    ]
    if not target_ids:
        raise ValueError("请选择要移动的贴纸。")

    with _lock:
        index = load_index()
        packs = index.setdefault("packs", {})
        source = packs.get(safe_source)
        if not source:
            raise ValueError("没有找到来源贴纸包。")
        target = _ensure_pack(index, safe_target)

        selected = set(target_ids)
        current_source_ids = [
            Path(str(sticker_id)).name
            for sticker_id in source.get("stickers") or []
            if Path(str(sticker_id)).name
        ]
        moved_ids = [sticker_id for sticker_id in current_source_ids if sticker_id in selected]
        if not moved_ids:
            return {"source": safe_source, "target": safe_target, "moved": 0}

        source["stickers"] = [sticker_id for sticker_id in current_source_ids if sticker_id not in selected]
        target_ids_existing = [
            Path(str(sticker_id)).name
            for sticker_id in target.get("stickers") or []
            if Path(str(sticker_id)).name
        ]
        for sticker_id in moved_ids:
            if sticker_id not in target_ids_existing:
                target_ids_existing.append(sticker_id)
        target["stickers"] = target_ids_existing

        save_index(index)
        return {"source": safe_source, "target": safe_target, "moved": len(moved_ids)}


def get_keyword_map() -> dict[str, list[str]]:
    index = load_index()
    keyword_map: dict[str, list[str]] = {}
    for pack_name, pack in (index.get("packs") or {}).items():
        for keyword in split_keywords(pack.get("keywords") or []):
            packs = keyword_map.setdefault(keyword, [])
            if pack_name not in packs:
                packs.append(pack_name)
    return {keyword: sorted(pack_names) for keyword, pack_names in sorted(keyword_map.items())}


def get_files_for_keyword(keyword: str) -> tuple[list[str], list[Path]]:
    index = load_index()
    keyword_map: dict[str, list[str]] = {}
    for pack_name, pack in (index.get("packs") or {}).items():
        for item in split_keywords(pack.get("keywords") or []):
            packs = keyword_map.setdefault(item, [])
            if pack_name not in packs:
                packs.append(pack_name)

    pack_names = sorted(keyword_map.get(str(keyword).strip(), []))
    files: list[Path] = []
    seen: set[Path] = set()
    for pack_name in pack_names:
        for path in _pack_files_from_index(index, pack_name, check_exists=True):
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                files.append(path)
    return pack_names, files


def get_state() -> dict[str, Any]:
    index = load_index()
    packs: list[dict[str, Any]] = []
    keyword_map: dict[str, list[str]] = {}
    stickers = index.get("stickers") or {}
    for pack_name, pack in sorted((index.get("packs") or {}).items()):
        keywords = split_keywords(pack.get("keywords") or [])
        preview_ids = [
            sticker_id
            for sticker_id in pack.get("stickers") or []
            if sticker_id in stickers
        ][:PACK_PREVIEW_LIMIT]
        packs.append({
            "name": pack_name,
            "count": len(pack.get("stickers") or []),
            "keywords": keywords,
            "previews": preview_ids,
        })
        for keyword in keywords:
            keyword_map.setdefault(keyword, []).append(pack_name)

    keywords = [
        {"keyword": keyword, "packs": sorted(pack_names)}
        for keyword, pack_names in sorted(keyword_map.items(), key=lambda item: item[0])
    ]
    return {
        "packs": packs,
        "keywords": keywords,
        "total_stickers": len(stickers),
    }


def register_pack_keywords(pack_name: str, keywords: Any = "", include_pack_name: bool = True) -> None:
    with _lock:
        index = load_index()
        _ensure_pack(index, pack_name)
        _add_keywords_to_index(index, pack_name, keywords, include_pack_name=include_pack_name)
        save_index(index)


def add_keywords(pack_name: str, keywords: Any) -> None:
    register_pack_keywords(pack_name, keywords, include_pack_name=False)


def remove_keyword(pack_name: str, keyword: str) -> bool:
    with _lock:
        index = load_index()
        pack = (index.get("packs") or {}).get(safe_pack_name(pack_name))
        if not pack:
            return False
        keywords = split_keywords(pack.get("keywords") or [])
        next_keywords = [item for item in keywords if item != keyword]
        if len(next_keywords) == len(keywords):
            return False
        pack["keywords"] = next_keywords
        save_index(index)
        return True


def delete_pack(pack_name: str) -> dict[str, Any]:
    safe_name = safe_pack_name(pack_name)
    if not safe_name:
        raise ValueError("贴纸包名称不能为空。")

    with _lock:
        index = load_index()
        packs = index.setdefault("packs", {})
        pack = packs.pop(safe_name, None)
        if not pack:
            return {
                "deleted": False,
                "pack": safe_name,
                "removed_stickers": 0,
                "deleted_files": 0,
            }

        removed_sticker_ids = [
            Path(str(sticker_id)).name
            for sticker_id in pack.get("stickers") or []
            if Path(str(sticker_id)).name
        ]
        still_referenced: set[str] = set()
        for other_pack in packs.values():
            for sticker_id in other_pack.get("stickers") or []:
                safe_id = Path(str(sticker_id)).name
                if safe_id:
                    still_referenced.add(safe_id)

        stickers = index.setdefault("stickers", {})
        deleted_files = 0
        for sticker_id in removed_sticker_ids:
            if sticker_id in still_referenced:
                continue
            path = _file_path_from_meta(stickers, sticker_id)
            stickers.pop(sticker_id, None)
            try:
                if path.is_file():
                    path.unlink()
                    deleted_files += 1
            except Exception as e:
                logger.warning("[StickerLibrary] 删除贴纸文件失败: %s -> %s", path, e)

        save_index(index)
        return {
            "deleted": True,
            "pack": safe_name,
            "removed_stickers": len(removed_sticker_ids),
            "deleted_files": deleted_files,
        }


def save_gifs_to_pack(pack_name: str, gif_paths: list[Path], *, source: str = "import") -> list[Path]:
    saved_paths: list[Path] = []
    with _lock:
        index = load_index()
        _ensure_pack(index, pack_name)
        for gif_path in gif_paths:
            if not gif_path.exists() or gif_path.stat().st_size <= 0:
                continue
            try:
                saved_path, _ = _add_file_to_index(
                    index,
                    pack_name,
                    gif_path,
                    source=source,
                    original_name=gif_path.name,
                )
            except Exception as e:
                logger.warning("[StickerLibrary] 保存贴纸失败: %s -> %s", gif_path, e)
                continue
            saved_paths.append(saved_path)
        save_index(index)
    return saved_paths


def save_gif_to_pack(pack_name: str, gif_path: Path, *, source: str = "upload", original_name: str = "") -> tuple[Path, bool]:
    with _lock:
        index = load_index()
        saved_path, created = _add_file_to_index(
            index,
            pack_name,
            gif_path,
            source=source,
            original_name=original_name or gif_path.name,
        )
        save_index(index)
        return saved_path, created
