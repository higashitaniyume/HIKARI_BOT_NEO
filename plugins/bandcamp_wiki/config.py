from __future__ import annotations

import logging
from typing import Any

from core.config_loader import load_plugin_config

logger = logging.getLogger("HikariBot.BandcampConfig")

DEFAULT_BANDCAMP_CONFIG: dict[str, Any] = {
    "enabled": True,
    "timeout": 15,
    "search_limit": 5,
    "searxng_url": "http://searxng-core:8080",
    "proxy": "",
    "user_agent": "{bot_name} bandcamp_search",
    "cross_reference": True,
    "netease_api_url": "http://192.168.31.2:5111",
}

_first_load_done = False


def get_config() -> dict[str, Any]:
    global _first_load_done
    cfg = load_plugin_config("bandcamp_wiki", DEFAULT_BANDCAMP_CONFIG)
    if not _first_load_done:
        _first_load_done = True
        logger.info(
            "Bandcamp 搜索配置加载完成 -> enabled=%s, search_limit=%s",
            cfg.get("enabled"),
            cfg.get("search_limit"),
        )
    return cfg
