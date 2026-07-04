from __future__ import annotations

import logging
from typing import Any

from core.config_loader import load_plugin_config

logger = logging.getLogger("HikariBot.ZhihuHotConfig")

DEFAULT_ZHIHU_HOT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "api_url": "https://api.zhihu.com/topstory/hot-list",
    "timeout_seconds": 20,
    "proxy": "",
    "user_agent": "Mozilla/5.0 {bot_name} Zhihu Hot Reader",
    "cache_ttl_minutes": 5,
    "max_items": 15,
    "summary_max_chars": 150,
    "cache_dir": "/tmp/hikari_bot/zhihu_hot",
    "request_params": {},
    "render": {
        "image_format": "PNG",
        "jpeg_quality": 86,
    },
}

_first_load_done = False


def get_config() -> dict[str, Any]:
    global _first_load_done
    cfg = load_plugin_config("zhihu_hot", DEFAULT_ZHIHU_HOT_CONFIG)
    if not _first_load_done:
        _first_load_done = True
        logger.info(
            "知乎热搜配置加载完成 -> enabled=%s, api=%s, max_items=%s",
            cfg.get("enabled"),
            cfg.get("api_url"),
            cfg.get("max_items"),
        )
    return cfg
