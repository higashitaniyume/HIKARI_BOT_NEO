from __future__ import annotations

import json
from typing import Any

from plugins.aiagent.config import get_config as get_aiagent_config
from plugins.aiagent.config import list_persona_skills as list_aiagent_persona_skills
from plugins.aiagent.config import resolve_persona_path as resolve_aiagent_persona_path
from plugins.aiagent.config import save_config as save_aiagent_config
from plugins.tts_speaker.config import DEFAULT_VOICES
from plugins.tts_speaker.config import get_config as get_tts_config
from plugins.tts_speaker.config import save_config as save_tts_config

from .parsing import (
    _parse_bool,
    _parse_fish_backup_model,
    _parse_fish_format,
    _parse_fish_latency,
    _parse_fish_model,
    _parse_float,
    _parse_int,
    _parse_mp3_bitrate,
    _parse_sample_rate,
    _parse_str,
    _parse_tts_voices,
)

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

