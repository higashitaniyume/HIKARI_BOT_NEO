from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("HikariBot.StickerCollector.Config")

CONFIG_PATH = Path("BotData/plugin_configs/sticker_collector.json")

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "collect_group": True,
    "collect_private": True,
    "allowed_groups": [],
    "ignored_users": [],
    "max_pending": 1000,
    "max_download_mb": 30,
    "temp_root": "/tmp/hikari_bot/sticker_collector",
    "download_timeout_seconds": 30,
}


def _write_config(data: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_config() -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        _write_config(DEFAULT_CONFIG)
        logger.info("已创建贴纸静默收集配置文件: %s", CONFIG_PATH)
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
        logger.info("已补全贴纸静默收集配置文件: %s", CONFIG_PATH)


def get_config() -> dict[str, Any]:
    ensure_config()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.exception("读取贴纸静默收集配置失败: %s", e)
        return DEFAULT_CONFIG.copy()

    cfg = DEFAULT_CONFIG.copy()
    cfg.update(data)
    return cfg
