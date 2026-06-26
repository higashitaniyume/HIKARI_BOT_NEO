from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("HikariBot.TTSSpeaker.Config")

CONFIG_PATH = Path("BotData/plugin_configs/tts_speaker.json")

DEFAULT_VOICES: list[dict[str, str]] = [
    {"name": "永雏塔菲", "reference_id": "55b28b196e1c4fff9a55cd32a46eff25"},
    {"name": "蒋介石", "reference_id": "918a8277663d476b95e2c4867da0f6a6"},
    {"name": "电棍", "reference_id": "703b0f7a5b7848f3bdfb7698ddb1899b"},
]

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "selected_voice": "永雏塔菲",
    "voices": DEFAULT_VOICES,
    "fish_audio": {
        "api_key": "",
        "model": "s2-pro",
        "format": "mp3",
        "latency": "normal",
        "speed": 1.0,
        "volume": 0.0,
        "normalize_loudness": True,
        "pitch_semitones": 0.0,
        "temperature": 0.7,
        "top_p": 0.7,
        "chunk_length": 300,
        "normalize": True,
        "sample_rate": None,
        "mp3_bitrate": 128,
        "repetition_penalty": 1.2,
        "condition_on_previous_chunks": True,
    },
    "proxy": "",
    "connect_timeout": 10,
    "receive_timeout": 60,
    "max_chars": 120,
    "cooldown_seconds": 5,
    "cache_dir": "/tmp/hikari_bot/tts",
    "cache_ttl_minutes": 60,
}


def _write_config(data: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_config() -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        _write_config(DEFAULT_CONFIG)
        logger.info("已创建 TTS 配置文件: %s", CONFIG_PATH)
        return

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return

    changed = False
    fish_cfg = data.get("fish_audio") if isinstance(data.get("fish_audio"), dict) else {}
    legacy_reference_id = str(fish_cfg.get("reference_id") or "").strip()

    if not isinstance(data.get("voices"), list) or not data["voices"]:
        voices = [voice.copy() for voice in DEFAULT_VOICES]
        if legacy_reference_id and all(voice["reference_id"] != legacy_reference_id for voice in voices):
            voices.append({"name": "原有音色", "reference_id": legacy_reference_id})
        data["voices"] = voices
        data["selected_voice"] = next(
            (voice["name"] for voice in voices if voice["reference_id"] == legacy_reference_id),
            "永雏塔菲",
        )
        changed = True

    for key, value in DEFAULT_CONFIG.items():
        if key not in data:
            data[key] = value
            changed = True
        elif isinstance(value, dict) and isinstance(data.get(key), dict):
            for nested_key, nested_value in value.items():
                if nested_key not in data[key]:
                    data[key][nested_key] = nested_value
                    changed = True

    # Remove obsolete Edge TTS fields when upgrading an existing installation.
    for key in ("default_provider", "voice", "rate", "volume", "pitch"):
        if key in data:
            data.pop(key)
            changed = True
    if isinstance(data.get("fish_audio"), dict):
        for key in ("enabled", "reference_id"):
            if key in data["fish_audio"]:
                data["fish_audio"].pop(key)
                changed = True
    if changed:
        _write_config(data)
        logger.info("已补全 TTS 配置文件: %s", CONFIG_PATH)


def get_config() -> dict[str, Any]:
    ensure_config()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.exception("读取 TTS 配置失败: %s", e)
        return DEFAULT_CONFIG.copy()

    cfg = DEFAULT_CONFIG.copy()
    if isinstance(data, dict):
        cfg.update(data)
    return cfg


def save_config(data: dict[str, Any]) -> dict[str, Any]:
    cfg = DEFAULT_CONFIG.copy()
    cfg.update(data)
    _write_config(cfg)
    return cfg.copy()
