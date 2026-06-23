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

_lock = threading.RLock()


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
            if sticker_id in stickers and sticker_id not in sticker_ids:
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
    with _lock:
        if LIBRARY_CONFIG_PATH.exists():
            index = _normalize_index(_read_json(LIBRARY_CONFIG_PATH))
            return index

        index = _empty_index()
        migrated = _migrate_legacy_dirs(index)
        _atomic_write_json(LIBRARY_CONFIG_PATH, index)
        _sync_legacy_trigger_config(index)
        logger.info("[StickerLibrary] 已初始化贴纸库，迁移旧贴纸 %d 个", migrated)
        return index


def save_index(index: dict[str, Any]) -> None:
    with _lock:
        normalized = _normalize_index(index)
        _atomic_write_json(LIBRARY_CONFIG_PATH, normalized)
        _sync_legacy_trigger_config(normalized)


def list_pack_names() -> list[str]:
    index = load_index()
    return sorted((index.get("packs") or {}).keys())


def get_pack_files(pack_name: str) -> list[Path]:
    index = load_index()
    pack = (index.get("packs") or {}).get(safe_pack_name(pack_name))
    if not pack:
        return []
    files: list[Path] = []
    stickers = index.get("stickers") or {}
    for sticker_id in pack.get("stickers") or []:
        meta = stickers.get(sticker_id) or {}
        path = _storage_path(str(meta.get("file") or sticker_id))
        if path.is_file() and path.suffix.lower() in MEDIA_EXTS and path.stat().st_size > 0:
            files.append(path)
    return sorted(files)


def get_packs_files(pack_names: list[str]) -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()
    for pack_name in pack_names:
        for path in get_pack_files(pack_name):
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                files.append(path)
    return files


def get_all_files() -> list[Path]:
    return get_packs_files(list_pack_names())


def count_pack(pack_name: str) -> int:
    return len(get_pack_files(pack_name))


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
    pack_names = get_keyword_map().get(str(keyword).strip(), [])
    return pack_names, get_packs_files(pack_names)


def get_state() -> dict[str, Any]:
    index = load_index()
    packs: list[dict[str, Any]] = []
    keyword_map: dict[str, list[str]] = {}
    for pack_name, pack in sorted((index.get("packs") or {}).items()):
        keywords = split_keywords(pack.get("keywords") or [])
        packs.append({
            "name": pack_name,
            "count": count_pack(pack_name),
            "keywords": keywords,
        })
        for keyword in keywords:
            keyword_map.setdefault(keyword, []).append(pack_name)

    keywords = [
        {"keyword": keyword, "packs": sorted(pack_names)}
        for keyword, pack_names in sorted(keyword_map.items(), key=lambda item: item[0])
    ]
    return {"packs": packs, "keywords": keywords}


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
