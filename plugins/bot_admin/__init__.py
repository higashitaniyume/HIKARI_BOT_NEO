from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
from email.parser import BytesParser
from email.policy import default as email_policy
from http.cookies import SimpleCookie
import json
import logging
import mimetypes
import os
import re
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from plugins import sticker_inbox
from plugins import sticker_library
from plugins import voice_library
from plugins.aiagent.config import get_config as get_aiagent_config
from plugins.aiagent.config import list_persona_skills as list_aiagent_persona_skills
from plugins.aiagent.config import resolve_persona_path as resolve_aiagent_persona_path
from plugins.aiagent.config import save_config as save_aiagent_config
from plugins.media_transcoder import STICKER_INPUT_EXTS, TranscodeError, ensure_sticker_gif
from plugins.tg_sticker_parser import find_saved_gifs, parse_sticker_set_to_gifs, save_gifs_to_pack
from plugins.tg_sticker_parser.config import get_config as get_tg_config
from plugins.tg_sticker_parser.tg_api import extract_sticker_set_names
from plugins.tts_speaker.config import DEFAULT_VOICES
from plugins.tts_speaker.config import get_config as get_tts_config
from plugins.tts_speaker.config import save_config as save_tts_config

from .config import get_config

logger = logging.getLogger("HikariBot.BotAdmin")

ALLOWED_EXTS = STICKER_INPUT_EXTS
VOICE_ALLOWED_EXTS = voice_library.MEDIA_EXTS
MAX_UPLOAD_FILES = 99
MAX_VOICE_UPLOAD_FILES = 20
_TEMPLATE_PATH = Path(__file__).parent / "templates" / "index.html"
_STATIC_ROOT = Path(__file__).parent / "static"
_COOKIE_NAME = "hikari_sticker_session"
_PLUGIN_CONFIG_DIR = Path("BotData/plugin_configs")
_LOG_DIR = Path("BotData/logs")
_MAX_CONFIG_EDIT_BYTES = 2 * 1024 * 1024
_MAX_LOG_TAIL_BYTES = 256 * 1024
_server_started = False
_server_lock = threading.Lock()
_upload_jobs: dict[str, dict[str, Any]] = {}
_upload_jobs_lock = threading.Lock()


def _safe_pack_name(value: str) -> str:
    value = value.strip()
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", value)
    value = value.strip(" ._")
    return value[:80]


def _safe_voice_name(value: str) -> str:
    return voice_library.safe_voice_name(value)


def _safe_filename(value: str) -> str:
    value = Path(value or "upload").name
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", value)
    value = value.strip(" ._")
    if not value:
        value = f"upload_{int(time.time())}.gif"
    return value[:120]


def _temp_root() -> Path:
    cfg = get_config()
    return Path(str(cfg.get("temp_root", "/tmp/hikari_bot/sticker_uploads")))


def _hash_content(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _split_keywords(value: Any) -> list[str]:
    return sticker_library.split_keywords(value)


def _register_trigger(pack_name: str, keyword: str = "") -> None:
    sticker_library.register_pack_keywords(pack_name, keyword, include_pack_name=True)


def _add_trigger_keyword(pack_name: str, keyword: str) -> None:
    sticker_library.add_keywords(pack_name, keyword)


def _remove_trigger_keyword(pack_name: str, keyword: str) -> bool:
    return sticker_library.remove_keyword(pack_name, keyword)


def _html_page(message: str = "") -> bytes:
    message_html = f'<div class="notice">{html.escape(message)}</div>' if message else ""
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    page = template.replace("<!-- MESSAGE_HTML -->", message_html)
    return page.encode("utf-8")


def _pack_state() -> dict[str, Any]:
    return sticker_library.get_state()


def _inbox_state() -> dict[str, Any]:
    return {"items": sticker_inbox.list_items()}


def _voice_state() -> dict[str, Any]:
    return voice_library.get_state()


def _tts_config_state() -> dict[str, Any]:
    cfg = get_tts_config()
    sanitized = json.loads(json.dumps(cfg, ensure_ascii=False))
    fish_cfg = sanitized.get("fish_audio") if isinstance(sanitized.get("fish_audio"), dict) else {}
    api_key = str(fish_cfg.get("api_key") or "")
    fish_cfg["api_key"] = ""
    fish_cfg["api_key_set"] = bool(api_key)
    sanitized["fish_audio"] = fish_cfg
    return {"config": sanitized}


def _aiagent_config_state() -> dict[str, Any]:
    cfg = get_aiagent_config()
    sanitized = json.loads(json.dumps(cfg, ensure_ascii=False))
    model_cfg = sanitized.get("model") if isinstance(sanitized.get("model"), dict) else {}
    api_key = str(model_cfg.get("api_key") or "")
    model_cfg["api_key"] = ""
    model_cfg["api_key_set"] = bool(api_key)
    sanitized["model"] = model_cfg
    return {
        "config": sanitized,
        "personas": list_aiagent_persona_skills(),
    }


def _json_bytes(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "启用"}


def _parse_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return min(max(parsed, minimum), maximum)


def _parse_fish_model(value: Any, default: str = "s2-pro") -> str:
    model = str(value or default).strip()
    if model in {"s1", "s2-pro", "s2.1-pro", "s2.1-pro-free"}:
        return model
    raise ValueError("Fish Audio 模型只能是 s1、s2-pro、s2.1-pro 或 s2.1-pro-free。")


def _parse_fish_backup_model(value: Any) -> str:
    model = str(value or "").strip()
    return "" if not model else _parse_fish_model(model)


def _parse_fish_format(value: Any, default: str = "mp3") -> str:
    fmt = str(value or default).strip().lower()
    if fmt in {"mp3", "wav", "opus", "pcm"}:
        return fmt
    raise ValueError("Fish Audio 输出格式只能是 mp3、wav、opus 或 pcm。")


def _parse_fish_latency(value: Any, default: str = "normal") -> str:
    latency = str(value or default).strip().lower()
    if latency in {"low", "normal", "balanced"}:
        return latency
    raise ValueError("Fish Audio 延迟模式只能是 low、normal 或 balanced。")


def _parse_float(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        return default
    return min(max(parsed, minimum), maximum)


def _parse_str(value: Any, default: str = "", *, max_length: int = 4000) -> str:
    text = str(value if value is not None else default).strip()
    return text[:max_length]


def _parse_sample_rate(value: Any) -> int | None:
    if value in (None, "", 0, "0", "auto"):
        return None
    return _parse_int(value, 44100, minimum=8000, maximum=192000)


def _parse_mp3_bitrate(value: Any) -> int:
    bitrate = _parse_int(value, 128, minimum=64, maximum=192)
    if bitrate not in {64, 128, 192}:
        raise ValueError("MP3 比特率只能是 64、128 或 192 kbps。")
    return bitrate


def _parse_tts_voices(value: Any, fallback: list[dict[str, str]]) -> list[dict[str, str]]:
    raw_voices = value if isinstance(value, list) else fallback
    voices: list[dict[str, str]] = []
    names: set[str] = set()
    for item in raw_voices:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        reference_id = str(item.get("reference_id") or "").strip()
        if not name or len(name) > 40 or not re.fullmatch(r"[A-Za-z0-9_-]{4,128}", reference_id):
            raise ValueError("音色名称或模型 ID 格式无效。")
        normalized_name = name.casefold()
        if normalized_name in names:
            raise ValueError(f"音色名称重复：{name}")
        names.add(normalized_name)
        voices.append({"name": name, "reference_id": reference_id})
    if not voices:
        raise ValueError("至少保留一个音色。")
    return voices


def _update_tts_config(data: dict[str, Any]) -> dict[str, Any]:
    current = get_tts_config()
    current_fish = current.get("fish_audio") if isinstance(current.get("fish_audio"), dict) else {}
    input_fish = data.get("fish_audio") if isinstance(data.get("fish_audio"), dict) else {}
    current_voices = current.get("voices") if isinstance(current.get("voices"), list) else DEFAULT_VOICES
    voices = _parse_tts_voices(data.get("voices", current_voices), current_voices)
    selected_voice = str(data.get("selected_voice", current.get("selected_voice", ""))).strip()
    if selected_voice not in {voice["name"] for voice in voices}:
        raise ValueError("请选择音色库中的一个音色。")

    fish_api_key = str(input_fish.get("api_key") or "").strip()
    if not fish_api_key:
        fish_api_key = str(current_fish.get("api_key") or "").strip()
    next_config = {
        "enabled": _parse_bool(data.get("enabled", current.get("enabled", True))),
        "selected_voice": selected_voice,
        "voices": voices,
        "fish_audio": {
            "api_key": fish_api_key,
            "model": _parse_fish_model(input_fish.get("model", current_fish.get("model", "s2-pro"))),
            "backup_model": _parse_fish_backup_model(input_fish.get("backup_model", current_fish.get("backup_model", "s2.1-pro-free"))),
            "retry_count": _parse_int(input_fish.get("retry_count", current_fish.get("retry_count", 3)), 3, minimum=0, maximum=5),
            "retry_delay_seconds": _parse_float(input_fish.get("retry_delay_seconds", current_fish.get("retry_delay_seconds", 1.0)), 1.0, minimum=0.1, maximum=30.0),
            "format": _parse_fish_format(input_fish.get("format", current_fish.get("format", "mp3"))),
            "latency": _parse_fish_latency(input_fish.get("latency", current_fish.get("latency", "normal"))),
            "speed": _parse_float(input_fish.get("speed", current_fish.get("speed", 1.0)), 1.0, minimum=0.5, maximum=2.0),
            "volume": _parse_float(input_fish.get("volume", current_fish.get("volume", 0.0)), 0.0, minimum=-24.0, maximum=24.0),
            "normalize_loudness": _parse_bool(input_fish.get("normalize_loudness", current_fish.get("normalize_loudness", True))),
            "pitch_semitones": _parse_float(input_fish.get("pitch_semitones", current_fish.get("pitch_semitones", 0.0)), 0.0, minimum=-12.0, maximum=12.0),
            "temperature": _parse_float(input_fish.get("temperature", current_fish.get("temperature", 0.7)), 0.7, minimum=0.0, maximum=1.0),
            "top_p": _parse_float(input_fish.get("top_p", current_fish.get("top_p", 0.7)), 0.7, minimum=0.0, maximum=1.0),
            "chunk_length": _parse_int(input_fish.get("chunk_length", current_fish.get("chunk_length", 300)), 300, minimum=100, maximum=300),
            "normalize": _parse_bool(input_fish.get("normalize", current_fish.get("normalize", True))),
            "sample_rate": _parse_sample_rate(input_fish.get("sample_rate", current_fish.get("sample_rate"))),
            "mp3_bitrate": _parse_mp3_bitrate(input_fish.get("mp3_bitrate", current_fish.get("mp3_bitrate", 128))),
            "repetition_penalty": _parse_float(input_fish.get("repetition_penalty", current_fish.get("repetition_penalty", 1.2)), 1.2, minimum=0.0, maximum=3.0),
            "condition_on_previous_chunks": _parse_bool(input_fish.get("condition_on_previous_chunks", current_fish.get("condition_on_previous_chunks", True))),
        },
        "proxy": str(data.get("proxy", current.get("proxy", ""))).strip(),
        "connect_timeout": _parse_int(
            data.get("connect_timeout", current.get("connect_timeout", 10)),
            10,
            minimum=1,
            maximum=300,
        ),
        "receive_timeout": _parse_int(
            data.get("receive_timeout", current.get("receive_timeout", 60)),
            60,
            minimum=1,
            maximum=600,
        ),
        "max_chars": _parse_int(
            data.get("max_chars", current.get("max_chars", 120)),
            120,
            minimum=1,
            maximum=1000,
        ),
        "cooldown_seconds": _parse_int(
            data.get("cooldown_seconds", current.get("cooldown_seconds", 5)),
            5,
            minimum=0,
            maximum=3600,
        ),
        "cache_dir": str(data.get("cache_dir", current.get("cache_dir", "/tmp/hikari_bot/tts"))).strip()
        or "/tmp/hikari_bot/tts",
        "cache_ttl_minutes": _parse_int(
            data.get("cache_ttl_minutes", current.get("cache_ttl_minutes", 60)),
            60,
            minimum=1,
            maximum=10080,
        ),
    }
    return save_tts_config(next_config)


def _update_aiagent_config(data: dict[str, Any]) -> dict[str, Any]:
    current = get_aiagent_config()
    current_model = current.get("model") if isinstance(current.get("model"), dict) else {}
    current_persona = current.get("persona") if isinstance(current.get("persona"), dict) else {}
    current_chat = current.get("chat") if isinstance(current.get("chat"), dict) else {}
    current_memory = current.get("memory") if isinstance(current.get("memory"), dict) else {}
    current_tools = current.get("tools") if isinstance(current.get("tools"), dict) else {}
    current_search = current_tools.get("search") if isinstance(current_tools.get("search"), dict) else {}
    current_files = current_tools.get("files") if isinstance(current_tools.get("files"), dict) else {}
    input_model = data.get("model") if isinstance(data.get("model"), dict) else {}
    input_persona = data.get("persona") if isinstance(data.get("persona"), dict) else {}
    input_chat = data.get("chat") if isinstance(data.get("chat"), dict) else {}
    input_memory = data.get("memory") if isinstance(data.get("memory"), dict) else {}
    input_tools = data.get("tools") if isinstance(data.get("tools"), dict) else {}
    input_search = input_tools.get("search") if isinstance(input_tools.get("search"), dict) else {}
    input_files = input_tools.get("files") if isinstance(input_tools.get("files"), dict) else {}

    api_key = _parse_str(input_model.get("api_key"), "", max_length=4096)
    if not api_key:
        api_key = str(current_model.get("api_key") or "")

    base_url = _parse_str(input_model.get("base_url", current_model.get("base_url", "")), max_length=512).rstrip("/")
    model_name = _parse_str(input_model.get("model", current_model.get("model", "")), max_length=160)
    if not base_url:
        raise ValueError("OpenAI-compatible API 地址不能为空。")
    if not model_name:
        raise ValueError("模型名称不能为空。")

    next_config = {
        "enabled": _parse_bool(data.get("enabled", current.get("enabled", False))),
        "model": {
            "base_url": base_url,
            "api_key": api_key,
            "model": model_name,
            "temperature": _parse_float(input_model.get("temperature", current_model.get("temperature", 0.7)), 0.7, minimum=0.0, maximum=2.0),
            "top_p": _parse_float(input_model.get("top_p", current_model.get("top_p", 1.0)), 1.0, minimum=0.0, maximum=1.0),
            "max_tokens": _parse_int(input_model.get("max_tokens", current_model.get("max_tokens", 1024)), 1024, minimum=1, maximum=32000),
            "timeout_seconds": _parse_int(input_model.get("timeout_seconds", current_model.get("timeout_seconds", 60)), 60, minimum=5, maximum=600),
            "proxy": _parse_str(input_model.get("proxy", current_model.get("proxy", "")), max_length=512),
        },
        "persona": {
            "skill_path": _parse_str(input_persona.get("skill_path", current_persona.get("skill_path", "BotData/agent_personas/default")), max_length=512),
            "max_chars": _parse_int(input_persona.get("max_chars", current_persona.get("max_chars", 12000)), 12000, minimum=1000, maximum=80000),
            "include_references": _parse_bool(input_persona.get("include_references", current_persona.get("include_references", True))),
            "reference_max_depth": _parse_int(input_persona.get("reference_max_depth", current_persona.get("reference_max_depth", 1)), 1, minimum=0, maximum=3),
            "reference_max_files": _parse_int(input_persona.get("reference_max_files", current_persona.get("reference_max_files", 8)), 8, minimum=0, maximum=32),
            "reference_max_chars_per_file": _parse_int(input_persona.get("reference_max_chars_per_file", current_persona.get("reference_max_chars_per_file", 8000)), 8000, minimum=1000, maximum=80000),
            "reference_max_total_chars": _parse_int(input_persona.get("reference_max_total_chars", current_persona.get("reference_max_total_chars", 24000)), 24000, minimum=1000, maximum=160000),
            "fallback_prompt": _parse_str(input_persona.get("fallback_prompt", current_persona.get("fallback_prompt", "")), max_length=20000),
        },
        "chat": {
            "max_user_chars": _parse_int(input_chat.get("max_user_chars", current_chat.get("max_user_chars", 2000)), 2000, minimum=1, maximum=20000),
            "max_reply_chars": _parse_int(input_chat.get("max_reply_chars", current_chat.get("max_reply_chars", 3500)), 3500, minimum=100, maximum=12000),
            "max_history_messages": _parse_int(input_chat.get("max_history_messages", current_chat.get("max_history_messages", 10)), 10, minimum=0, maximum=40),
            "cooldown_seconds": _parse_int(input_chat.get("cooldown_seconds", current_chat.get("cooldown_seconds", 3)), 3, minimum=0, maximum=3600),
            "system_prompt_extra": _parse_str(input_chat.get("system_prompt_extra", current_chat.get("system_prompt_extra", "")), max_length=20000),
            "blocked_url_domains": current_chat.get("blocked_url_domains", []),
        },
        "memory": {
            "enabled": _parse_bool(input_memory.get("enabled", current_memory.get("enabled", True))),
            "root": _parse_str(input_memory.get("root", current_memory.get("root", "UserData/aiagent_memory")), max_length=512),
            "max_read_chars_per_file": _parse_int(input_memory.get("max_read_chars_per_file", current_memory.get("max_read_chars_per_file", 8000)), 8000, minimum=1000, maximum=80000),
            "max_file_chars": _parse_int(input_memory.get("max_file_chars", current_memory.get("max_file_chars", 60000)), 60000, minimum=5000, maximum=500000),
        },
        "tools": {
            "search": {
                "enabled": _parse_bool(input_search.get("enabled", current_search.get("enabled", True))),
                "base_url": _parse_str(input_search.get("base_url", current_search.get("base_url", "http://searxng-core:8080")), max_length=512),
                "timeout_seconds": _parse_int(input_search.get("timeout_seconds", current_search.get("timeout_seconds", 15)), 15, minimum=1, maximum=120),
                "max_results": _parse_int(input_search.get("max_results", current_search.get("max_results", 5)), 5, minimum=1, maximum=10),
                "safesearch": _parse_int(input_search.get("safesearch", current_search.get("safesearch", 1)), 1, minimum=0, maximum=2),
                "language": _parse_str(input_search.get("language", current_search.get("language", "auto")), max_length=32),
                "categories": _parse_str(input_search.get("categories", current_search.get("categories", "general")), max_length=160),
            },
            "files": {
                "enabled": _parse_bool(input_files.get("enabled", current_files.get("enabled", True))),
                "max_read_chars": _parse_int(input_files.get("max_read_chars", current_files.get("max_read_chars", 20000)), 20000, minimum=1000, maximum=200000),
                "max_write_chars": _parse_int(input_files.get("max_write_chars", current_files.get("max_write_chars", 20000)), 20000, minimum=1000, maximum=200000),
            },
            "max_tool_rounds": _parse_int(input_tools.get("max_tool_rounds", current_tools.get("max_tool_rounds", 2)), 2, minimum=0, maximum=5),
        },
    }
    resolve_aiagent_persona_path(next_config["persona"]["skill_path"])
    return save_aiagent_config(next_config)


def _safe_config_file(name: str) -> Path:
    raw_name = unquote(str(name or "")).strip()
    safe_name = Path(raw_name).name
    if raw_name != safe_name or not safe_name.endswith(".json") or safe_name.startswith("."):
        raise ValueError("配置文件名无效。")
    path = (_PLUGIN_CONFIG_DIR / safe_name).resolve()
    root = _PLUGIN_CONFIG_DIR.resolve()
    if root not in path.parents or not path.is_file():
        raise ValueError("配置文件不存在。")
    return path


def _safe_log_file(name: str) -> Path:
    raw_name = unquote(str(name or "")).strip()
    safe_name = Path(raw_name).name
    if raw_name != safe_name or not safe_name.endswith(".log") or safe_name.startswith("."):
        raise ValueError("日志文件名无效。")
    path = (_LOG_DIR / safe_name).resolve()
    root = _LOG_DIR.resolve()
    if root not in path.parents or not path.is_file():
        raise ValueError("日志文件不存在。")
    return path


def _file_meta(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
    }


def _list_plugin_configs() -> dict[str, Any]:
    _PLUGIN_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    files = [
        _file_meta(path)
        for path in sorted(_PLUGIN_CONFIG_DIR.glob("*.json"), key=lambda item: item.name.casefold())
        if path.is_file() and not path.name.endswith(".example.json")
    ]
    return {
        "files": files,
        "max_edit_bytes": _MAX_CONFIG_EDIT_BYTES,
    }


def _read_plugin_config(name: str) -> dict[str, Any]:
    path = _safe_config_file(name)
    size = path.stat().st_size
    if size > _MAX_CONFIG_EDIT_BYTES:
        raise ValueError(f"配置文件太大，暂不支持在线编辑：{path.name}")
    text = path.read_text(encoding="utf-8")
    try:
        json.loads(text or "{}")
    except json.JSONDecodeError as e:
        raise ValueError(f"配置文件 JSON 格式错误：{e}") from e
    return {
        "file": _file_meta(path),
        "content": text,
    }


def _write_plugin_config(name: str, content: str) -> dict[str, Any]:
    path = _safe_config_file(name)
    if len(content.encode("utf-8")) > _MAX_CONFIG_EDIT_BYTES:
        raise ValueError("配置内容太大，拒绝保存。")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 格式错误：{e}") from e
    if not isinstance(parsed, dict):
        raise ValueError("配置文件顶层必须是 JSON 对象。")

    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)
    return _read_plugin_config(path.name)


def _list_logs() -> dict[str, Any]:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    files = [
        _file_meta(path)
        for path in sorted(_LOG_DIR.glob("*.log"), key=lambda item: item.stat().st_mtime, reverse=True)
        if path.is_file()
    ]
    return {
        "files": files,
        "max_tail_bytes": _MAX_LOG_TAIL_BYTES,
    }


def _read_log_tail(name: str, max_bytes: Any = _MAX_LOG_TAIL_BYTES) -> dict[str, Any]:
    path = _safe_log_file(name)
    try:
        requested = int(max_bytes)
    except Exception:
        requested = _MAX_LOG_TAIL_BYTES
    limit = min(max(requested, 1024), _MAX_LOG_TAIL_BYTES)
    size = path.stat().st_size
    with path.open("rb") as f:
        if size > limit:
            f.seek(size - limit)
        content = f.read(limit)
    return {
        "file": _file_meta(path),
        "truncated": size > limit,
        "content": content.decode("utf-8", errors="replace"),
    }


def _new_upload_job(pack_name: str, total: int) -> dict[str, Any]:
    now = time.time()
    job = {
        "id": uuid.uuid4().hex,
        "status": "queued",
        "pack": pack_name,
        "total": total,
        "processed": 0,
        "saved": 0,
        "reused": 0,
        "failed": [],
        "current": "",
        "message": "等待处理...",
        "created_at": now,
        "updated_at": now,
    }
    with _upload_jobs_lock:
        _upload_jobs[job["id"]] = job
    return job.copy()


def _update_upload_job(job_id: str, **updates: Any) -> None:
    with _upload_jobs_lock:
        job = _upload_jobs.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updated_at"] = time.time()


def _get_upload_job(job_id: str) -> dict[str, Any] | None:
    with _upload_jobs_lock:
        job = _upload_jobs.get(job_id)
        return job.copy() if job else None


def _process_upload_files(
    pack_name: str,
    keyword: str,
    file_infos: list[dict[str, Any]],
    job_id: str | None = None,
) -> dict[str, Any]:
    _register_trigger(pack_name, keyword)

    temp_dir = _temp_root()
    temp_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    reused: list[str] = []
    failed: list[str] = []

    if job_id:
        _update_upload_job(job_id, status="running", message="开始处理...")

    for index, file_info in enumerate(file_infos, start=1):
        filename = _safe_filename(str(file_info["filename"]))
        if job_id:
            _update_upload_job(
                job_id,
                current=filename,
                processed=index - 1,
                saved=len(saved),
                reused=len(reused),
                failed=failed.copy(),
                message=f"正在处理 {index}/{len(file_infos)}：{filename}",
            )

        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_EXTS:
            failed.append(f"{filename}：不支持的文件格式 {suffix or '(无后缀)'}")
            if job_id:
                _update_upload_job(job_id, processed=index, failed=failed.copy())
            continue

        content = file_info["content"]
        content_hash = _hash_content(content)

        temp_path: Path | None = None
        temp_gif_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=suffix,
                prefix=f"{content_hash[:16]}_",
                dir=temp_dir,
                delete=False,
            ) as temp_file:
                temp_file.write(content)
                temp_path = Path(temp_file.name)

            temp_gif_path = temp_dir / f"{content_hash[:16]}_{uuid.uuid4().hex}.gif"
            asyncio.run(ensure_sticker_gif(temp_path, temp_gif_path))
            saved_path, created = sticker_library.save_gif_to_pack(
                pack_name,
                temp_gif_path,
                source="upload",
                original_name=filename,
            )
            if created:
                saved.append(saved_path.name)
            else:
                reused.append(saved_path.name)
        except TranscodeError as e:
            failed.append(f"{filename}：转 GIF 失败：{e}")
        except Exception as e:
            logger.exception("贴纸上传处理失败: %s", e)
            failed.append(f"{filename}：处理失败，请检查服务日志")
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            if temp_gif_path is not None:
                temp_gif_path.unlink(missing_ok=True)

        if job_id:
            _update_upload_job(
                job_id,
                processed=index,
                saved=len(saved),
                reused=len(reused),
                failed=failed.copy(),
            )

    details = [f"上传完成：{pack_name}"]
    if saved:
        details.append(f"新增 {len(saved)} 个")
    if reused:
        details.append(f"复用 {len(reused)} 个")
    if failed:
        details.append(f"失败 {len(failed)} 个：{'；'.join(failed[:5])}")
        if len(failed) > 5:
            details.append(f"另有 {len(failed) - 5} 个失败项已省略")

    status = "failed" if failed and not saved and not reused else "done"
    summary = "，".join(details)
    if job_id:
        _update_upload_job(
            job_id,
            status=status,
            current="",
            processed=len(file_infos),
            saved=len(saved),
            reused=len(reused),
            failed=failed.copy(),
            message=summary,
        )

    return {
        "status": status,
        "message": summary,
        "saved": saved,
        "reused": reused,
        "failed": failed,
    }


def _process_voice_uploads(
    display_name: str,
    keyword: str,
    file_infos: list[dict[str, Any]],
) -> dict[str, Any]:
    temp_dir = _temp_root() / "voices"
    temp_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    reused: list[str] = []
    failed: list[str] = []

    for file_info in file_infos:
        filename = _safe_filename(str(file_info["filename"]))
        suffix = Path(filename).suffix.lower()
        if suffix not in VOICE_ALLOWED_EXTS:
            failed.append(f"{filename}：不支持的语音格式 {suffix or '(无后缀)'}")
            continue

        temp_path: Path | None = None
        try:
            content = file_info["content"]
            with tempfile.NamedTemporaryFile(
                suffix=suffix,
                prefix=f"voice_{uuid.uuid4().hex}_",
                dir=temp_dir,
                delete=False,
            ) as temp_file:
                temp_file.write(content)
                temp_path = Path(temp_file.name)

            voice_name = display_name if len(file_infos) == 1 else Path(filename).stem
            saved_path, created = voice_library.save_voice_file(
                temp_path,
                display_name=voice_name,
                keywords=keyword,
                original_name=filename,
            )
            if created:
                saved.append(saved_path.name)
            else:
                reused.append(saved_path.name)
        except Exception as e:
            logger.exception("语音上传处理失败: %s", e)
            failed.append(f"{filename}：处理失败，请检查服务日志")
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    details = ["语音上传完成"]
    if saved:
        details.append(f"新增 {len(saved)} 个")
    if reused:
        details.append(f"复用 {len(reused)} 个")
    if failed:
        details.append(f"失败 {len(failed)} 个：{'；'.join(failed[:5])}")
        if len(failed) > 5:
            details.append(f"另有 {len(failed) - 5} 个失败项已省略")

    return {
        "status": "failed" if failed and not saved and not reused else "done",
        "message": "，".join(details),
        "saved": saved,
        "reused": reused,
        "failed": failed,
        "state": _voice_state(),
    }


async def _process_tg_sticker_link_async(
    link: str,
    pack_name: str,
    keyword: str,
    refresh: bool,
    job_id: str,
) -> None:
    set_names = extract_sticker_set_names(link)
    if not set_names:
        _update_upload_job(
            job_id,
            status="failed",
            message="没有识别到 Telegram 贴纸包链接。",
        )
        return

    set_name = set_names[0]
    target_pack = pack_name or set_name
    cfg = get_tg_config()
    _update_upload_job(
        job_id,
        status="running",
        pack=target_pack,
        current=set_name,
        message=f"准备导入 Telegram 贴纸包：{set_name}",
    )

    cached_pack = target_pack
    cached_gifs = find_saved_gifs(target_pack)
    if not cached_gifs:
        cached_pack = set_name
        cached_gifs = find_saved_gifs(set_name)
    if cached_gifs and not refresh:
        saved_paths = cached_gifs if cached_pack == target_pack else save_gifs_to_pack(target_pack, cached_gifs)
        _register_trigger(target_pack, keyword)
        _update_upload_job(
            job_id,
            status="done",
            total=len(cached_gifs),
            processed=len(cached_gifs),
            saved=len(saved_paths),
            reused=len(cached_gifs),
            current="",
            message=f"已从本地缓存导入：{target_pack}，共 {len(saved_paths)} 个 GIF。",
        )
        return

    def report_progress(progress: dict[str, Any]) -> None:
        total = int(progress.get("total") or 0)
        processed = int(progress.get("processed") or 0)
        _update_upload_job(
            job_id,
            total=total,
            processed=processed,
            current=str(progress.get("title") or set_name),
            message=str(progress.get("message") or "正在导入 Telegram 贴纸包..."),
        )

    try:
        result = await parse_sticker_set_to_gifs(
            bot=None,
            event=None,
            set_name=set_name,
            cfg=cfg,
            progress_callback=report_progress,
        )
        gif_paths = result.get("gif_paths") or []
        total_count = int(result.get("total_count") or len(gif_paths))
        failed_count = int(result.get("failed_count") or 0)
        failed_items = [str(item) for item in result.get("failed_items") or []]

        if not gif_paths:
            _update_upload_job(
                job_id,
                status="failed",
                total=total_count,
                processed=total_count,
                failed=[f"{set_name}：没有成功转换出可保存的 GIF"],
                current="",
                message="没有成功转换出可保存的 GIF。",
            )
            return

        saved_paths = save_gifs_to_pack(target_pack, gif_paths)
        _register_trigger(target_pack, keyword)
        message = f"Telegram 贴纸包导入完成：{target_pack}，新增/覆盖 {len(saved_paths)} 个"
        if failed_count:
            message += f"，失败 {failed_count} 个"
            if failed_items:
                message += f"：{'；'.join(failed_items[:3])}"
                if len(failed_items) > 3:
                    message += f"；另有 {len(failed_items) - 3} 个失败项已省略"
        _update_upload_job(
            job_id,
            status="done",
            total=total_count,
            processed=total_count,
            saved=len(saved_paths),
            reused=0,
            failed=failed_items if failed_items else ([] if failed_count <= 0 else [f"转换失败 {failed_count} 个"]),
            current="",
            message=message,
        )
    except Exception as e:
        logger.exception("Telegram 贴纸包导入失败: %s", e)
        _update_upload_job(
            job_id,
            status="failed",
            current="",
            failed=[str(e)],
            message=f"Telegram 贴纸包导入失败：{e}",
        )


def _process_tg_sticker_link(link: str, pack_name: str, keyword: str, refresh: bool, job_id: str) -> None:
    asyncio.run(_process_tg_sticker_link_async(link, pack_name, keyword, refresh, job_id))


def _auth_password() -> str:
    return str(get_config().get("password", "")).strip()


def _auth_enabled() -> bool:
    return bool(_auth_password())


def _session_ttl_seconds() -> int:
    try:
        ttl = int(get_config().get("session_ttl_seconds", 604800))
    except Exception:
        return 604800
    return max(60, ttl)


def _make_session_token(timestamp: int | None = None) -> str:
    timestamp = timestamp or int(time.time())
    payload = str(timestamp)
    signature = hmac.new(_auth_password().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def _valid_session_token(token: str) -> bool:
    if not _auth_enabled():
        return True
    try:
        raw_timestamp, signature = token.split(".", 1)
        timestamp = int(raw_timestamp)
    except Exception:
        return False

    if timestamp <= 0 or time.time() - timestamp > _session_ttl_seconds():
        return False

    expected = hmac.new(_auth_password().encode("utf-8"), raw_timestamp.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def _login_page(message: str = "") -> bytes:
    escaped = html.escape(message)
    error_html = f'<div class="toast error">{escaped}</div>' if message else ""
    page = f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HIKARI 贴纸管理登录</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
<main class="shell auth-shell">
  <section class="panel auth-panel">
    <p class="eyebrow">HIKARI Bot Console</p>
    <h1>输入管理密码</h1>
    {error_html}
    <form action="/login" method="post" class="login-form">
      <label>
        <span>密码</span>
        <input name="password" type="password" autocomplete="current-password" autofocus required>
      </label>
      <button type="submit" class="primary">登录</button>
    </form>
  </section>
</main>
</body>
</html>'''
    return page.encode("utf-8")


class BotAdminHandler(BaseHTTPRequestHandler):
    server_version = "HikariBotAdmin/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("[BotAdmin] " + fmt, *args)

    def _send_html(self, body: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self._write_body(body)

    def _write_body(self, body: bytes) -> None:
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            logger.info("[BotAdmin] 客户端在响应写入前断开连接")

    def _redirect(self, location: str, cookie: str | None = None) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def _is_authenticated(self) -> bool:
        if not _auth_enabled():
            return True
        cookie_header = self.headers.get("Cookie", "")
        cookie = SimpleCookie(cookie_header)
        morsel = cookie.get(_COOKIE_NAME)
        return bool(morsel and _valid_session_token(morsel.value))

    def _send_login(self, message: str = "", status: int = 200) -> None:
        self._send_html(_login_page(message), status)

    def _unauthorized_json(self) -> None:
        self._send_json({"error": "请先登录。"}, 401)

    def _read_form_body(self) -> dict[str, str]:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as e:
            raise ValueError("请求格式错误：Content-Length 无效。") from e
        body = self.rfile.read(max(content_length, 0)).decode("utf-8", errors="replace")
        values = parse_qs(body)
        return {key: value[-1] for key, value in values.items() if value}

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = _json_bytes(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self._write_body(body)

    def _send_static(self, parsed_path: str) -> None:
        relative = unquote(parsed_path.removeprefix("/static/")).replace("\\", "/")
        if not relative or relative.startswith("/") or ".." in Path(relative).parts:
            self._send_html(_html_page("静态资源不存在。"), 404)
            return

        path = _STATIC_ROOT / relative
        if not path.is_file():
            self._send_html(_html_page("静态资源不存在。"), 404)
            return

        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".js":
            content_type = "text/javascript"
        elif path.suffix == ".css":
            content_type = "text/css"

        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self._write_body(body)

    def _send_sticker(self, sticker_id: str) -> None:
        safe_id = Path(unquote(sticker_id or "")).name
        if not safe_id or safe_id != unquote(sticker_id or ""):
            self._send_json({"error": "贴纸不存在。"}, 404)
            return

        path = sticker_library.get_sticker_path(safe_id)
        if path is None:
            self._send_json({"error": "贴纸不存在。"}, 404)
            return

        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "image/gif"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=86400")
        self.end_headers()
        self._write_body(body)

    def _send_voice_file(self, voice_id: str) -> None:
        safe_id = Path(unquote(voice_id or "")).name
        if not safe_id or safe_id != unquote(voice_id or ""):
            self._send_json({"error": "语音不存在。"}, 404)
            return

        path = voice_library.get_voice_path(safe_id)
        if path is None:
            self._send_json({"error": "语音不存在。"}, 404)
            return

        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "audio/mpeg"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=86400")
        self.end_headers()
        self._write_body(body)

    def _send_inbox_item(self, item_id: str) -> None:
        safe_id = Path(unquote(item_id or "")).name
        if not safe_id or safe_id != unquote(item_id or ""):
            self._send_json({"error": "收集项不存在。"}, 404)
            return

        path = sticker_inbox.get_item_path(safe_id)
        if path is None:
            self._send_json({"error": "收集项不存在。"}, 404)
            return

        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/gif")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=86400")
        self.end_headers()
        self._write_body(body)

    def _read_json_body(self) -> dict[str, Any]:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as e:
            raise ValueError("请求格式错误：Content-Length 无效。") from e
        if content_length <= 0:
            raise ValueError("请求内容为空。")
        try:
            data = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError("请求格式错误：JSON 无效。") from e
        if not isinstance(data, dict):
            raise ValueError("请求格式错误：需要 JSON 对象。")
        return data

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/static/"):
            self._send_static(parsed.path)
            return
        if parsed.path == "/login":
            if self._is_authenticated():
                self._redirect("/")
            else:
                self._send_login()
            return
        if parsed.path == "/logout":
            expired = f"{_COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
            self._redirect("/login", expired)
            return
        if parsed.path == "/api/state":
            if not self._is_authenticated():
                self._unauthorized_json()
                return
            self._send_json(_pack_state())
            return
        if parsed.path == "/api/inbox":
            if not self._is_authenticated():
                self._unauthorized_json()
                return
            self._send_json(_inbox_state())
            return
        if parsed.path == "/api/voice-state":
            if not self._is_authenticated():
                self._unauthorized_json()
                return
            self._send_json(_voice_state())
            return
        if parsed.path == "/api/tts-config":
            if not self._is_authenticated():
                self._unauthorized_json()
                return
            self._send_json(_tts_config_state())
            return
        if parsed.path == "/api/aiagent-config":
            if not self._is_authenticated():
                self._unauthorized_json()
                return
            self._send_json(_aiagent_config_state())
            return
        if parsed.path == "/api/configs":
            if not self._is_authenticated():
                self._unauthorized_json()
                return
            self._send_json(_list_plugin_configs())
            return
        if parsed.path.startswith("/api/configs/"):
            if not self._is_authenticated():
                self._unauthorized_json()
                return
            try:
                name = parsed.path.removeprefix("/api/configs/").strip("/")
                self._send_json(_read_plugin_config(name))
            except ValueError as e:
                self._send_json({"error": str(e)}, 400)
            except Exception as e:
                logger.exception("读取插件配置失败: %s", e)
                self._send_json({"error": "读取插件配置失败，请检查服务日志。"}, 500)
            return
        if parsed.path == "/api/logs":
            if not self._is_authenticated():
                self._unauthorized_json()
                return
            self._send_json(_list_logs())
            return
        if parsed.path.startswith("/api/logs/"):
            if not self._is_authenticated():
                self._unauthorized_json()
                return
            try:
                name = parsed.path.removeprefix("/api/logs/").strip("/")
                params = parse_qs(parsed.query)
                max_bytes = params.get("max_bytes", [_MAX_LOG_TAIL_BYTES])[0]
                self._send_json(_read_log_tail(name, max_bytes))
            except ValueError as e:
                self._send_json({"error": str(e)}, 400)
            except Exception as e:
                logger.exception("读取日志失败: %s", e)
                self._send_json({"error": "读取日志失败，请检查服务日志。"}, 500)
            return
        if parsed.path.startswith("/api/inbox/") and parsed.path.endswith("/image"):
            if not self._is_authenticated():
                self._unauthorized_json()
                return
            item_id = parsed.path.removeprefix("/api/inbox/").removesuffix("/image").strip("/")
            self._send_inbox_item(item_id)
            return
        if parsed.path.startswith("/api/voices/") and parsed.path.endswith("/file"):
            if not self._is_authenticated():
                self._unauthorized_json()
                return
            voice_id = parsed.path.removeprefix("/api/voices/").removesuffix("/file").strip("/")
            self._send_voice_file(voice_id)
            return
        if parsed.path.startswith("/api/stickers/"):
            if not self._is_authenticated():
                self._unauthorized_json()
                return
            sticker_id = parsed.path.removeprefix("/api/stickers/").strip("/")
            self._send_sticker(sticker_id)
            return
        if parsed.path.startswith("/api/uploads/"):
            if not self._is_authenticated():
                self._unauthorized_json()
                return
            job_id = parsed.path.removeprefix("/api/uploads/").strip("/")
            job = _get_upload_job(job_id)
            if job is None:
                self._send_json({"error": "上传任务不存在。"}, 404)
                return
            self._send_json(job)
            return
        if parsed.path not in {"/", "/index.html"}:
            self._send_html(_html_page("页面不存在。"), 404)
            return
        if not self._is_authenticated():
            self._send_login()
            return
        message = parse_qs(parsed.query).get("msg", [""])[0]
        self._send_html(_html_page(message))

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/login":
            try:
                fields = self._read_form_body()
            except ValueError as e:
                self._send_login(str(e), 400)
                return
            password = fields.get("password", "")
            if _auth_enabled() and hmac.compare_digest(password, _auth_password()):
                max_age = _session_ttl_seconds()
                cookie = f"{_COOKIE_NAME}={_make_session_token()}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax"
                self._redirect("/", cookie)
                return
            if not _auth_enabled():
                self._redirect("/")
                return
            self._send_login("密码不正确。", 401)
            return

        if not self._is_authenticated():
            if path.startswith("/api/"):
                self._unauthorized_json()
            else:
                self._send_login("请先登录。", 401)
            return
        if path.startswith("/api/configs/"):
            try:
                data = self._read_json_body()
                name = path.removeprefix("/api/configs/").strip("/")
                content = str(data.get("content", ""))
                result = _write_plugin_config(name, content)
                self._send_json({"config": result, "message": "配置已保存。"})
            except ValueError as e:
                self._send_json({"error": str(e)}, 400)
            except Exception as e:
                logger.exception("保存插件配置失败: %s", e)
                self._send_json({"error": "保存插件配置失败，请检查服务日志。"}, 500)
            return

        if path == "/api/tts-config":
            try:
                data = self._read_json_body()
                _update_tts_config(data)
                payload = _tts_config_state()
                payload["message"] = "TTS 设置已保存。"
                self._send_json(payload)
            except ValueError as e:
                self._send_json({"error": str(e)}, 400)
            except Exception as e:
                logger.exception("保存 TTS 设置失败: %s", e)
                self._send_json({"error": "保存 TTS 设置失败，请检查服务日志。"}, 500)
            return

        if path == "/api/aiagent-config":
            try:
                data = self._read_json_body()
                _update_aiagent_config(data)
                payload = _aiagent_config_state()
                payload["message"] = "AI Agent 设置已保存。"
                self._send_json(payload)
            except ValueError as e:
                self._send_json({"error": str(e)}, 400)
            except Exception as e:
                logger.exception("保存 AI Agent 设置失败: %s", e)
                self._send_json({"error": "保存 AI Agent 设置失败，请检查服务日志。"}, 500)
            return

        if path == "/api/voice-keywords":
            try:
                data = self._read_json_body()
                voice_id = Path(str(data.get("voice", ""))).name
                keyword = str(data.get("keyword", "")).strip()
                if not voice_id or not voice_library.split_keywords(keyword):
                    raise ValueError("语音和关键词都不能为空。")
                voice_library.add_keywords(voice_id, keyword)
                self._send_json(_voice_state())
            except ValueError as e:
                self._send_json({"error": str(e)}, 400)
            except Exception as e:
                logger.exception("新增语音关键词失败: %s", e)
                self._send_json({"error": "新增语音关键词失败，请检查服务日志。"}, 500)
            return

        if path == "/api/voices":
            try:
                fields, files = self._parse_multipart_form()
            except ValueError as e:
                self._send_json({"error": str(e)}, 400)
                return

            display_name = _safe_voice_name(fields.get("voice_name", ""))
            keyword = fields.get("voice_keyword", "").strip()
            file_infos = [file_info for file_info in files.get("voice_file", []) if file_info.get("filename")]
            if not file_infos:
                self._send_json({"error": "请选择要上传的语音文件。"}, 400)
                return
            if len(file_infos) > MAX_VOICE_UPLOAD_FILES:
                self._send_json({"error": f"一次最多上传 {MAX_VOICE_UPLOAD_FILES} 个语音文件。"}, 400)
                return

            result = _process_voice_uploads(display_name, keyword, file_infos)
            status = 400 if result["status"] == "failed" else 200
            self._send_json(result, status)
            return

        if path == "/api/keywords":
            try:
                data = self._read_json_body()
                pack_name = _safe_pack_name(str(data.get("pack", "")))
                keyword = str(data.get("keyword", "")).strip()
                if not pack_name or not _split_keywords(keyword):
                    raise ValueError("贴纸包和关键词都不能为空。")
                _add_trigger_keyword(pack_name, keyword)
                self._send_json(_pack_state())
            except ValueError as e:
                self._send_json({"error": str(e)}, 400)
            except Exception as e:
                logger.exception("新增贴纸关键词失败: %s", e)
                self._send_json({"error": "新增贴纸关键词失败，请检查服务日志。"}, 500)
            return

        if path == "/api/tg-stickers":
            try:
                data = self._read_json_body()
                link = str(data.get("url", "")).strip()
                set_names = extract_sticker_set_names(link)
                if not set_names:
                    raise ValueError("请输入有效的 Telegram 贴纸包链接。")

                pack_name = _safe_pack_name(str(data.get("pack", "")))
                target_pack = pack_name or set_names[0]
                keyword = str(data.get("keyword", "")).strip()
                refresh = bool(data.get("refresh", False))
                job = _new_upload_job(target_pack, 0)
                _update_upload_job(
                    job["id"],
                    status="queued",
                    current=set_names[0],
                    message=f"已创建 Telegram 导入任务：{set_names[0]}",
                )
                thread = threading.Thread(
                    target=_process_tg_sticker_link,
                    args=(link, target_pack, keyword, refresh, job["id"]),
                    name=f"StickerTgImport-{job['id'][:8]}",
                    daemon=True,
                )
                thread.start()
                self._send_json(_get_upload_job(job["id"]) or job, 202)
            except ValueError as e:
                self._send_json({"error": str(e)}, 400)
            except Exception as e:
                logger.exception("创建 Telegram 贴纸导入任务失败: %s", e)
                self._send_json({"error": "创建 Telegram 贴纸导入任务失败，请检查服务日志。"}, 500)
            return

        if path == "/api/inbox/assign":
            try:
                data = self._read_json_body()
                item_ids = [str(item_id) for item_id in data.get("ids") or [] if str(item_id).strip()]
                pack_name = _safe_pack_name(str(data.get("pack", "")))
                keyword = str(data.get("keyword", "")).strip()
                if not item_ids:
                    raise ValueError("请选择要整理的表情。")
                if not pack_name:
                    raise ValueError("请选择或输入目标贴纸包。")
                result = sticker_inbox.assign_items(item_ids, pack_name, keyword)
                self._send_json({"result": result, "inbox": _inbox_state(), "state": _pack_state()})
            except ValueError as e:
                self._send_json({"error": str(e)}, 400)
            except Exception as e:
                logger.exception("整理收集箱贴纸失败: %s", e)
                self._send_json({"error": "整理收集箱贴纸失败，请检查服务日志。"}, 500)
            return

        if path == "/api/inbox/delete":
            try:
                data = self._read_json_body()
                item_ids = [str(item_id) for item_id in data.get("ids") or [] if str(item_id).strip()]
                if not item_ids:
                    raise ValueError("请选择要删除的表情。")
                removed = sticker_inbox.delete_items(item_ids)
                self._send_json({"removed": removed, "inbox": _inbox_state()})
            except ValueError as e:
                self._send_json({"error": str(e)}, 400)
            except Exception as e:
                logger.exception("删除收集箱贴纸失败: %s", e)
                self._send_json({"error": "删除收集箱贴纸失败，请检查服务日志。"}, 500)
            return

        if path not in {"/upload", "/api/uploads"}:
            self._send_html(_html_page("页面不存在。"), 404)
            return

        try:
            fields, files = self._parse_multipart_form()
        except ValueError as e:
            if path == "/api/uploads":
                self._send_json({"error": str(e)}, 400)
                return
            self._send_html(_html_page(str(e)), 400)
            return

        existing_pack = _safe_pack_name(fields.get("existing_pack", ""))
        new_pack = _safe_pack_name(fields.get("new_pack", ""))
        keyword = fields.get("keyword", "").strip()
        pack_name = existing_pack or new_pack

        if not pack_name:
            if path == "/api/uploads":
                self._send_json({"error": "请先选择已有贴纸包，或输入新贴纸包名称。"}, 400)
                return
            self._send_html(_html_page("请先选择已有贴纸包，或输入新贴纸包名称。"), 400)
            return

        file_infos = [file_info for file_info in files.get("file", []) if file_info.get("filename")]
        if not file_infos:
            if path == "/api/uploads":
                self._send_json({"error": "请选择要上传的文件。"}, 400)
                return
            self._send_html(_html_page("请选择要上传的文件。"), 400)
            return

        if len(file_infos) > MAX_UPLOAD_FILES:
            if path == "/api/uploads":
                self._send_json({"error": f"一次最多上传 {MAX_UPLOAD_FILES} 个文件。"}, 400)
                return
            self._send_html(_html_page(f"一次最多上传 {MAX_UPLOAD_FILES} 个文件。"), 400)
            return

        if path == "/api/uploads":
            job = _new_upload_job(pack_name, len(file_infos))
            thread = threading.Thread(
                target=_process_upload_files,
                args=(pack_name, keyword, file_infos, job["id"]),
                name=f"StickerUpload-{job['id'][:8]}",
                daemon=True,
            )
            thread.start()
            self._send_json(job, 202)
            return

        result = _process_upload_files(pack_name, keyword, file_infos)
        status = 400 if result["status"] == "failed" else 200
        self._send_html(_html_page(result["message"]), status)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if not self._is_authenticated():
            self._unauthorized_json()
            return
        params = parse_qs(parsed.query)

        if parsed.path == "/api/packs":
            pack_name = _safe_pack_name(params.get("pack", [""])[0])
            if not pack_name:
                self._send_json({"error": "贴纸包不能为空。"}, 400)
                return

            try:
                result = sticker_library.delete_pack(pack_name)
                payload = _pack_state()
                payload["result"] = result
                if not result.get("deleted"):
                    payload["error"] = "没有找到这个贴纸包。"
                    self._send_json(payload, 404)
                    return
                self._send_json(payload)
            except ValueError as e:
                self._send_json({"error": str(e)}, 400)
            except Exception as e:
                logger.exception("删除贴纸包失败: %s", e)
                self._send_json({"error": "删除贴纸包失败，请检查服务日志。"}, 500)
            return

        if parsed.path == "/api/voices":
            voice_id = Path(params.get("voice", [""])[0]).name
            if not voice_id:
                self._send_json({"error": "语音不能为空。"}, 400)
                return

            try:
                result = voice_library.delete_voice(voice_id)
                payload = _voice_state()
                payload["result"] = result
                if not result.get("deleted"):
                    payload["error"] = "没有找到这个语音。"
                    self._send_json(payload, 404)
                    return
                self._send_json(payload)
            except ValueError as e:
                self._send_json({"error": str(e)}, 400)
            except Exception as e:
                logger.exception("删除语音失败: %s", e)
                self._send_json({"error": "删除语音失败，请检查服务日志。"}, 500)
            return

        if parsed.path == "/api/voice-keywords":
            voice_id = Path(params.get("voice", [""])[0]).name
            keyword = params.get("keyword", [""])[0].strip()
            if not voice_id or not keyword:
                self._send_json({"error": "语音和关键词都不能为空。"}, 400)
                return

            removed = voice_library.remove_keyword(voice_id, keyword)
            status = 200 if removed else 404
            payload = _voice_state()
            if not removed:
                payload["error"] = "没有找到这个关键词关联。"
            self._send_json(payload, status)
            return

        if parsed.path != "/api/keywords":
            self._send_json({"error": "页面不存在。"}, 404)
            return

        pack_name = _safe_pack_name(params.get("pack", [""])[0])
        keyword = params.get("keyword", [""])[0].strip()
        if not pack_name or not keyword:
            self._send_json({"error": "贴纸包和关键词都不能为空。"}, 400)
            return

        removed = _remove_trigger_keyword(pack_name, keyword)
        status = 200 if removed else 404
        payload = _pack_state()
        if not removed:
            payload["error"] = "没有找到这个关键词关联。"
        self._send_json(payload, status)

    def _parse_multipart_form(self) -> tuple[dict[str, str], dict[str, list[dict[str, Any]]]]:
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type.lower():
            raise ValueError("请求格式错误：需要 multipart/form-data。")

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as e:
            raise ValueError("请求格式错误：Content-Length 无效。") from e

        if content_length <= 0:
            raise ValueError("上传内容为空。")

        body = self.rfile.read(content_length)
        raw_message = (
            f"Content-Type: {content_type}\r\n"
            "MIME-Version: 1.0\r\n\r\n"
        ).encode("utf-8") + body
        message = BytesParser(policy=email_policy).parsebytes(raw_message)

        if not message.is_multipart():
            raise ValueError("请求格式错误：未找到 multipart 内容。")

        fields: dict[str, str] = {}
        files: dict[str, list[dict[str, Any]]] = {}

        for part in message.iter_parts():
            disposition = part.get("Content-Disposition", "")
            if "form-data" not in disposition:
                continue

            name = part.get_param("name", header="Content-Disposition")
            if not name:
                continue

            filename = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            if filename:
                files.setdefault(name, []).append({
                    "filename": filename,
                    "content": payload,
                })
            else:
                charset = part.get_content_charset() or "utf-8"
                fields[name] = payload.decode(charset, errors="replace")

        return fields, files


def _normalize_port(raw_port: Any) -> int:
    try:
        port = int(raw_port)
    except Exception:
        logger.warning("Bot 后台端口无效，使用默认端口 54213: %r", raw_port)
        return 54213

    if not 1 <= port <= 65535:
        logger.warning("Bot 后台端口 %s 超出范围，使用默认端口 54213", port)
        return 54213
    return port


def start_server() -> None:
    global _server_started
    cfg = get_config()
    if not cfg.get("enabled", True):
        logger.info("Bot 后台已关闭")
        return

    with _server_lock:
        if _server_started:
            return
        host = str(cfg.get("host", "0.0.0.0"))
        port = _normalize_port(cfg.get("port", 54213))
        try:
            server = ThreadingHTTPServer((host, port), BotAdminHandler)
        except OSError as e:
            logger.error("Bot 后台启动失败: %s:%s → %s", host, port, e)
            return

        thread = threading.Thread(target=server.serve_forever, name="BotAdminServer", daemon=True)
        thread.start()
        _server_started = True
        logger.info("Bot 后台已启动: http://%s:%s/", host, port)


start_server()


