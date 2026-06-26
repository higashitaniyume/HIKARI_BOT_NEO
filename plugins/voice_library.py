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

logger = logging.getLogger("HikariBot.VoiceLibrary")

VOICE_CONFIG_PATH = Path("BotData/plugin_configs/voice_trigger.json")
STORAGE_ROOT = Path("BotData/Voices/_library")
MEDIA_EXTS = {".silk", ".amr", ".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac", ".opus"}

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


def safe_voice_name(value: str) -> str:
    value = str(value or "").strip()
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", value)
    value = value.strip(" ._")
    return value[:80]


def _empty_index() -> dict[str, Any]:
    return {
        "version": 1,
        "storage_root": str(STORAGE_ROOT).replace("\\", "/"),
        "voices": {},
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("[VoiceLibrary] 读取 JSON 失败，将忽略: %s -> %s", path, e)
        return {}


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def _normalize_index(index: dict[str, Any]) -> dict[str, Any]:
    normalized = _empty_index()
    normalized.update({k: v for k, v in index.items() if k != "voices"})
    normalized["version"] = 1
    normalized["storage_root"] = str(STORAGE_ROOT).replace("\\", "/")

    voices: dict[str, Any] = {}
    for voice_id, meta in (index.get("voices") or {}).items():
        voice_id = Path(str(voice_id)).name
        if not voice_id:
            continue
        meta = meta if isinstance(meta, dict) else {}
        file_name = Path(str(meta.get("file") or voice_id)).name
        suffix = Path(file_name).suffix.lower()
        if suffix not in MEDIA_EXTS:
            continue
        display_name = safe_voice_name(str(meta.get("display_name") or Path(file_name).stem))
        voices[voice_id] = {
            "file": file_name,
            "sha256": str(meta.get("sha256") or "").strip(),
            "display_name": display_name or Path(file_name).stem,
            "original_name": str(meta.get("original_name") or file_name),
            "keywords": split_keywords(meta.get("keywords") or []),
            "created_at": int(meta.get("created_at") or time.time()),
        }

    normalized["voices"] = voices
    return normalized


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _voice_id_for_hash(content_hash: str, suffix: str) -> str:
    suffix = suffix.lower()
    return f"{content_hash[:16]}{suffix}"


def _storage_path(voice_id: str) -> Path:
    return STORAGE_ROOT / Path(voice_id).name


def _file_path_from_meta(voices: dict[str, Any], voice_id: str) -> Path:
    meta = voices.get(voice_id) or {}
    return _storage_path(str(meta.get("file") or voice_id))


def load_index() -> dict[str, Any]:
    global _index_cache, _index_cache_mtime_ns
    with _lock:
        if VOICE_CONFIG_PATH.exists():
            mtime_ns = VOICE_CONFIG_PATH.stat().st_mtime_ns
            if _index_cache is not None and _index_cache_mtime_ns == mtime_ns:
                return _index_cache
            index = _normalize_index(_read_json(VOICE_CONFIG_PATH))
            _index_cache = index
            _index_cache_mtime_ns = mtime_ns
            return index

        index = _empty_index()
        _atomic_write_json(VOICE_CONFIG_PATH, index)
        _index_cache = index
        _index_cache_mtime_ns = VOICE_CONFIG_PATH.stat().st_mtime_ns
        logger.info("[VoiceLibrary] 已初始化语音库")
        return index


def save_index(index: dict[str, Any]) -> None:
    global _index_cache, _index_cache_mtime_ns
    with _lock:
        normalized = _normalize_index(index)
        _atomic_write_json(VOICE_CONFIG_PATH, normalized)
        _index_cache = normalized
        _index_cache_mtime_ns = VOICE_CONFIG_PATH.stat().st_mtime_ns


def get_voice_path(voice_id: str) -> Path | None:
    index = load_index()
    safe_id = Path(str(voice_id or "")).name
    if not safe_id or safe_id not in (index.get("voices") or {}):
        return None
    path = _file_path_from_meta(index.get("voices") or {}, safe_id)
    if path.is_file() and path.suffix.lower() in MEDIA_EXTS and path.stat().st_size > 0:
        return path
    return None


def get_voices_for_keyword(keyword: str) -> list[Path]:
    index = load_index()
    matched: list[Path] = []
    for voice_id, meta in sorted((index.get("voices") or {}).items()):
        if str(keyword).strip() not in split_keywords(meta.get("keywords") or []):
            continue
        path = get_voice_path(voice_id)
        if path is not None:
            matched.append(path)
    return matched


def get_state() -> dict[str, Any]:
    index = load_index()
    voices: list[dict[str, Any]] = []
    keyword_map: dict[str, list[str]] = {}
    total_bytes = 0

    for voice_id, meta in sorted((index.get("voices") or {}).items(), key=lambda item: item[1].get("display_name", item[0])):
        path = get_voice_path(voice_id)
        size = path.stat().st_size if path is not None else 0
        total_bytes += size
        keywords = split_keywords(meta.get("keywords") or [])
        display_name = str(meta.get("display_name") or Path(str(meta.get("file") or voice_id)).stem)
        voices.append({
            "id": voice_id,
            "name": display_name,
            "file": str(meta.get("file") or voice_id),
            "original_name": str(meta.get("original_name") or voice_id),
            "keywords": keywords,
            "created_at": int(meta.get("created_at") or 0),
            "size": size,
            "missing": path is None,
        })
        for keyword in keywords:
            keyword_map.setdefault(keyword, []).append(display_name)

    keywords = [
        {"keyword": keyword, "voices": sorted(names)}
        for keyword, names in sorted(keyword_map.items(), key=lambda item: item[0])
    ]
    return {
        "voices": voices,
        "keywords": keywords,
        "total_voices": len(voices),
        "total_keywords": len(keywords),
        "total_bytes": total_bytes,
        "allowed_exts": sorted(MEDIA_EXTS),
    }


def save_voice_file(
    source_path: Path,
    *,
    display_name: str = "",
    keywords: Any = "",
    original_name: str = "",
) -> tuple[Path, bool]:
    source_path = Path(source_path)
    suffix = source_path.suffix.lower()
    if suffix not in MEDIA_EXTS:
        raise ValueError(f"不支持的语音格式: {suffix or '(无后缀)'}")
    if not source_path.exists() or source_path.stat().st_size <= 0:
        raise ValueError(f"语音文件无效: {source_path}")

    with _lock:
        index = load_index()
        STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
        content_hash = _hash_file(source_path)
        voice_id = _voice_id_for_hash(content_hash, suffix)
        dest = _storage_path(voice_id)
        created = False
        if not dest.exists() or dest.stat().st_size <= 0:
            shutil.copy2(source_path, dest)
            created = True

        voices = index.setdefault("voices", {})
        meta = voices.setdefault(voice_id, {
            "file": voice_id,
            "sha256": content_hash,
            "display_name": safe_voice_name(display_name) or Path(original_name or source_path.name).stem,
            "original_name": original_name or source_path.name,
            "keywords": [],
            "created_at": int(time.time()),
        })
        if display_name:
            meta["display_name"] = safe_voice_name(display_name) or meta.get("display_name") or Path(voice_id).stem
        if original_name:
            meta["original_name"] = original_name

        current_keywords = split_keywords(meta.get("keywords") or [])
        for keyword in split_keywords(keywords):
            if keyword not in current_keywords:
                current_keywords.append(keyword)
        meta["keywords"] = current_keywords

        save_index(index)
        return dest, created


def add_keywords(voice_id: str, keywords: Any) -> None:
    with _lock:
        index = load_index()
        safe_id = Path(str(voice_id or "")).name
        voices = index.setdefault("voices", {})
        meta = voices.get(safe_id)
        if not meta:
            raise ValueError("语音不存在。")
        current_keywords = split_keywords(meta.get("keywords") or [])
        for keyword in split_keywords(keywords):
            if keyword not in current_keywords:
                current_keywords.append(keyword)
        meta["keywords"] = current_keywords
        save_index(index)


def remove_keyword(voice_id: str, keyword: str) -> bool:
    with _lock:
        index = load_index()
        safe_id = Path(str(voice_id or "")).name
        meta = (index.get("voices") or {}).get(safe_id)
        if not meta:
            return False
        keywords = split_keywords(meta.get("keywords") or [])
        next_keywords = [item for item in keywords if item != keyword]
        if len(next_keywords) == len(keywords):
            return False
        meta["keywords"] = next_keywords
        save_index(index)
        return True


def delete_voice(voice_id: str) -> dict[str, Any]:
    safe_id = Path(str(voice_id or "")).name
    if not safe_id:
        raise ValueError("语音不能为空。")

    with _lock:
        index = load_index()
        voices = index.setdefault("voices", {})
        meta = voices.pop(safe_id, None)
        if not meta:
            return {"deleted": False, "voice": safe_id, "deleted_file": False}

        path = _file_path_from_meta({safe_id: meta}, safe_id)
        deleted_file = False
        try:
            if path.is_file():
                path.unlink()
                deleted_file = True
        except Exception as e:
            logger.warning("[VoiceLibrary] 删除语音文件失败: %s -> %s", path, e)

        save_index(index)
        return {"deleted": True, "voice": safe_id, "deleted_file": deleted_file}
