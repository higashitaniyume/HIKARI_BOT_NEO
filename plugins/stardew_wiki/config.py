from __future__ import annotations

import logging
from typing import Any

from core.config_loader import load_plugin_config

logger = logging.getLogger("HikariBot.StardewWikiConfig")

DEFAULT_STARDEW_WIKI_CONFIG: dict[str, Any] = {
    "enabled": True,
    "api_url": "https://zh.stardewvalleywiki.com/mediawiki/api.php",
    "timeout": 12,
    "search_limit": 3,
    "summary_max_chars": 220,
    "detail_max_chars": 1600,
    "image_size": 640,
    "proxy": "",
}

_first_load_done = False


def get_config() -> dict[str, Any]:
    global _first_load_done
    cfg = load_plugin_config("stardew_wiki", DEFAULT_STARDEW_WIKI_CONFIG)
    if not _first_load_done:
        _first_load_done = True
        logger.info(
            "星露谷 Wiki 配置加载完成 -> enabled=%s, api_url=%s",
            cfg.get("enabled"),
            cfg.get("api_url"),
        )
    return cfg
