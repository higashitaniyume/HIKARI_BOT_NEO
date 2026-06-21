"""
表情包触发配置模块。

从 BotData/plugin_configs/sticker_trigger.json 读取配置。
支持热重载。
"""

import logging
from typing import Any

from core.config_loader import load_plugin_config, DEFAULT_STICKER_CONFIG

logger = logging.getLogger("HikariBot.StickerConfig")

_first_load_done = False


def get_config() -> dict[str, Any]:
    """获取表情包触发配置（每次从磁盘读取，支持热重载）。"""
    global _first_load_done
    cfg = load_plugin_config("sticker_trigger", DEFAULT_STICKER_CONFIG)
    if not _first_load_done:
        _first_load_done = True
        triggers = cfg.get("triggers", {})
        total_keywords = sum(len(v) for v in triggers.values())
        logger.info(f"表情包触发配置加载完成 → {len(triggers)} 个贴纸包, {total_keywords} 个关键词: {list(triggers.keys())}")
    return cfg
