from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("HikariBot.TTSSpeaker.Config")

CONFIG_PATH = Path("BotData/plugin_configs/tts_speaker.json")

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "default_provider": "edge",
    "voice": "zh-CN-XiaoxiaoNeural",
    "rate": "+0%",
    "volume": "+0%",
    "pitch": "+0Hz",
    "fish_audio": {
        "enabled": True,
        "api_key": "",
        "reference_id": "55b28b196e1c4fff9a55cd32a46eff25",
        "model": "s2-pro",
        "format": "mp3",
        "latency": "normal",
        "speed": 1.0,
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
    for key, value in DEFAULT_CONFIG.items():
        if key not in data:
            data[key] = value
            changed = True
        elif isinstance(value, dict) and isinstance(data.get(key), dict):
            for nested_key, nested_value in value.items():
                if nested_key not in data[key]:
                    data[key][nested_key] = nested_value
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
