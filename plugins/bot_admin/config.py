from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("HikariBot.BotAdmin.Config")

CONFIG_PATH = Path("BotData/plugin_configs/bot_admin.json")
LEGACY_CONFIG_PATH = Path("BotData/plugin_configs/sticker_web.json")

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "host": "0.0.0.0",
    "port": 54213,
    "upload_root": "BotData/Gifs",
    "temp_root": "/tmp/hikari_bot/sticker_uploads",
    "password": "change-me",
    "session_ttl_seconds": 604800,
}


def _write_config(data: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_config() -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        if LEGACY_CONFIG_PATH.exists():
            try:
                data = json.loads(LEGACY_CONFIG_PATH.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    cfg = DEFAULT_CONFIG.copy()
                    cfg.update(data)
                    _write_config(cfg)
                    logger.info("已从旧贴纸页面配置迁移 Bot 后台配置: %s", CONFIG_PATH)
                    return
            except Exception as e:
                logger.warning("迁移旧贴纸页面配置失败，将创建默认 Bot 后台配置: %s", e)
        _write_config(DEFAULT_CONFIG)
        logger.info("已创建 Bot 后台配置文件: %s", CONFIG_PATH)
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
    if changed:
        _write_config(data)
        logger.info("已补全 Bot 后台配置文件: %s", CONFIG_PATH)


def get_config() -> dict[str, Any]:
    ensure_config()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.exception("读取 Bot 后台配置失败: %s", e)
        return DEFAULT_CONFIG.copy()

    cfg = DEFAULT_CONFIG.copy()
    cfg.update(data)
    return cfg
