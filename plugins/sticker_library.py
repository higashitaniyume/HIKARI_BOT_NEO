from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("HikariBot.StickerLibrary")

LIBRARY_CONFIG_PATH = Path("BotData/plugin_configs/sticker_library.json")
LEGACY_TRIGGER_CONFIG_PATH = Path("BotData/plugin_configs/sticker_trigger.json")
LEGACY_ROOT = Path("BotData/Gifs")
STORAGE_ROOT = LEGACY_ROOT / "_library"
MEDIA_EXTS = {".gif"}
PACK_PREVIEW_LIMIT = 6

_lock = threading.RLock()
_index_cache: dict[str, Any] | None = None
_index_cache_mtime_ns: int | None = None


def split_keywords(value: Any) -> list[str]:
    keywords: list[str] = []
    raw_values = value if isinstance(value, list) else [value]
    for raw_value in raw_values:
        for keyword in re.split(r"[;；]+", str(raw_value or "")):
            keyword = keyword.strip()
            if keyword and keyword not in keywords:
                keywords.append(keyword)
    return keywords


def safe_pack_name(value: str) -> str:
    value = str(value or "").strip()
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", value)
    value = value.strip(" ._")
    return value[:80]


def _empty_index() -> dict[str, Any]:
    return {
        "version": 1,
        "storage_root": str(STORAGE_ROOT).replace("\\", "/"),
        "stickers": {},
        "packs": {},
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("[StickerLibrary] 读取 JSON 失败，将忽略: %s -> %s", path, e)
        return {}


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def _normalize_index(index: dict[str, Any]) -> dict[str, Any]:
    normalized = _empty_index()
    normalized.update({k: v for k, v in index.items() if k not in {"stickers", "packs"}})
    normalized["version"] = 1
    normalized["storage_root"] = str(STORAGE_ROOT).replace("\\", "/")

    stickers: dict[str, Any] = {}
    for sticker_id, meta in (index.get("stickers") or {}).items():
        sticker_id = Path(str(sticker_id)).name
        if not sticker_id:
            continue
        meta = meta if isinstance(meta, dict) else {}
        file_name = Path(str(meta.get("file") or sticker_id)).name
        if Path(file_name).suffix.lower() not in MEDIA_EXTS:
            continue
        stickers[sticker_id] = {
            "file": file_name,
            "sha256": str(meta.get("sha256") or "").strip(),
            "source": str(meta.get("source") or "unknown"),
            "original_name": str(meta.get("original_name") or file_name),
            "created_at": int(meta.get("created_at") or time.time()),
        }

    packs: dict[str, Any] = {}
    for pack_name, pack in (index.get("packs") or {}).items():
        pack_name = safe_pack_name(str(pack_name))
        if not pack_name:
            continue
        pack = pack if isinstance(pack, dict) else {}
        keywords = split_keywords(pack.get("keywords") or [])
        sticker_ids: list[str] = []
        for sticker_id in pack.get("stickers") or []:
            sticker_id = Path(str(sticker_id)).name
            if not sticker_id or Path(sticker_id).suffix.lower() not in MEDIA_EXTS:
                continue
            if sticker_id not in stickers:
                path = _storage_path(sticker_id)
                if path.is_file() and path.stat().st_size > 0:
                    stickers[sticker_id] = {
                        "file": sticker_id,
                        "sha256": _hash_file(path),
                        "source": "recovered",
                        "original_name": sticker_id,
                        "created_at": int(path.stat().st_mtime),
                    }
            if sticker_id not in sticker_ids:
                sticker_ids.append(sticker_id)
        packs[pack_name] = {"keywords": keywords, "stickers": sticker_ids}

    normalized["stickers"] = stickers
    normalized["packs"] = packs
    return normalized


def _read_legacy_triggers() -> dict[str, list[str]]:
    data = _read_json(LEGACY_TRIGGER_CONFIG_PATH)
    triggers = data.get("triggers") or {}
    if not isinstance(triggers, dict):
        return {}
    return {
        safe_pack_name(str(pack_name)): split_keywords(keywords)
        for pack_name, keywords in triggers.items()
        if safe_pack_name(str(pack_name))
    }


def _sync_legacy_trigger_config(index: dict[str, Any]) -> None:
    triggers = {
        pack_name: split_keywords(pack.get("keywords") or [])
        for pack_name, pack in sorted((index.get("packs") or {}).items())
    }
    _atomic_write_json(LEGACY_TRIGGER_CONFIG_PATH, {"triggers": triggers})


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sticker_id_for_hash(content_hash: str) -> str:
    return f"{content_hash[:16]}.gif"


def _storage_path(sticker_id: str) -> Path:
    return STORAGE_ROOT / Path(sticker_id).name


def _file_path_from_meta(stickers: dict[str, Any], sticker_id: str) -> Path:
    meta = stickers.get(sticker_id) or {}
    return _storage_path(str(meta.get("file") or sticker_id))


def _pack_files_from_index(index: dict[str, Any], pack_name: str, *, check_exists: bool = True) -> list[Path]:
    pack = (index.get("packs") or {}).get(safe_pack_name(pack_name))
    if not pack:
        return []

    files: list[Path] = []
    stickers = index.get("stickers") or {}
    for sticker_id in pack.get("stickers") or []:
        path = _file_path_from_meta(stickers, str(sticker_id))
        if not check_exists or (path.is_file() and path.suffix.lower() in MEDIA_EXTS and path.stat().st_size > 0):
            files.append(path)
    return sorted(files)


def _ensure_pack(index: dict[str, Any], pack_name: str) -> dict[str, Any]:
    safe_name = safe_pack_name(pack_name)
    if not safe_name:
        raise ValueError("贴纸包名称不能为空。")
    packs = index.setdefault("packs", {})
    pack = packs.setdefault(safe_name, {"keywords": [], "stickers": []})
    pack.setdefault("keywords", [])
    pack.setdefault("stickers", [])
    return pack


def _add_keywords_to_index(index: dict[str, Any], pack_name: str, keywords: Any, include_pack_name: bool = False) -> None:
    safe_name = safe_pack_name(pack_name)
    pack = _ensure_pack(index, safe_name)
    candidates = split_keywords(keywords)
    if include_pack_name:
        candidates = [safe_name, *candidates]
    for keyword in candidates:
        if keyword and keyword not in pack["keywords"]:
            pack["keywords"].append(keyword)


def _add_file_to_index(
    index: dict[str, Any],
    pack_name: str,
    source_path: Path,
    *,
    source: str,
    original_name: str = "",
) -> tuple[Path, bool]:
    if not source_path.exists() or source_path.stat().st_size <= 0:
        raise ValueError(f"贴纸文件无效: {source_path}")
    if source_path.suffix.lower() not in MEDIA_EXTS:
        raise ValueError(f"贴纸文件必须是 GIF: {source_path}")

    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    content_hash = _hash_file(source_path)
    sticker_id = _sticker_id_for_hash(content_hash)
    dest = _storage_path(sticker_id)
    created = False
    if not dest.exists() or dest.stat().st_size <= 0:
        shutil.copy2(source_path, dest)
        created = True

    stickers = index.setdefault("stickers", {})
    stickers.setdefault(sticker_id, {
        "file": sticker_id,
        "sha256": content_hash,
        "source": source,
        "original_name": original_name or source_path.name,
        "created_at": int(time.time()),
    })

    pack = _ensure_pack(index, pack_name)
    if sticker_id not in pack["stickers"]:
        pack["stickers"].append(sticker_id)
    return dest, created


def _migrate_legacy_dirs(index: dict[str, Any]) -> int:
    legacy_triggers = _read_legacy_triggers()
    migrated = 0

    for pack_name, keywords in legacy_triggers.items():
        _ensure_pack(index, pack_name)
        _add_keywords_to_index(index, pack_name, keywords, include_pack_name=False)

    if not LEGACY_ROOT.is_dir():
        return migrated

    for folder in sorted(LEGACY_ROOT.iterdir()):
        if not folder.is_dir() or folder.name == STORAGE_ROOT.name or folder.name.startswith("."):
            continue
        pack_name = safe_pack_name(folder.name)
        if not pack_name:
            continue
        _ensure_pack(index, pack_name)
        if pack_name in legacy_triggers:
            _add_keywords_to_index(index, pack_name, legacy_triggers[pack_name], include_pack_name=False)
        for path in sorted(folder.iterdir()):
            if not path.is_file() or path.suffix.lower() not in MEDIA_EXTS or path.stat().st_size <= 0:
                continue
            try:
                _add_file_to_index(index, pack_name, path, source="legacy", original_name=path.name)
                migrated += 1
            except Exception as e:
                logger.warning("[StickerLibrary] 迁移旧贴纸失败: %s -> %s", path, e)

    return migrated


def load_index() -> dict[str, Any]:
    global _index_cache, _index_cache_mtime_ns
    with _lock:
        if LIBRARY_CONFIG_PATH.exists():
            mtime_ns = LIBRARY_CONFIG_PATH.stat().st_mtime_ns
            if _index_cache is not None and _index_cache_mtime_ns == mtime_ns:
                return _index_cache
            index = _normalize_index(_read_json(LIBRARY_CONFIG_PATH))
            _index_cache = index
            _index_cache_mtime_ns = mtime_ns
            return index

        index = _empty_index()
        migrated = _migrate_legacy_dirs(index)
        _atomic_write_json(LIBRARY_CONFIG_PATH, index)
        _sync_legacy_trigger_config(index)
        _index_cache = index
        _index_cache_mtime_ns = LIBRARY_CONFIG_PATH.stat().st_mtime_ns
        logger.info("[StickerLibrary] 已初始化贴纸库，迁移旧贴纸 %d 个", migrated)
        return index


def save_index(index: dict[str, Any]) -> None:
    global _index_cache, _index_cache_mtime_ns
    with _lock:
        normalized = _normalize_index(index)
        _atomic_write_json(LIBRARY_CONFIG_PATH, normalized)
        _sync_legacy_trigger_config(normalized)
        _index_cache = normalized
        _index_cache_mtime_ns = LIBRARY_CONFIG_PATH.stat().st_mtime_ns


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
