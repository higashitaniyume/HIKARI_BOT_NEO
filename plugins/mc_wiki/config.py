from __future__ import annotations

import logging
from typing import Any

from core.config_loader import load_plugin_config

logger = logging.getLogger("HikariBot.McWikiConfig")

DEFAULT_MC_WIKI_CONFIG: dict[str, Any] = {
    "enabled": True,
    "api_url": "https://zh.minecraft.wiki/api.php",
    "timeout": 12,
    "search_limit": 3,
    "summary_max_chars": 220,
    "proxy": "",
    "user_agent": "HIKARI_BOT_NEO mc_wiki",
}

_first_load_done = False


def get_config() -> dict[str, Any]:
    global _first_load_done
    cfg = load_plugin_config("mc_wiki", DEFAULT_MC_WIKI_CONFIG)
    if not _first_load_done:
        _first_load_done = True
        logger.info(
            "Minecraft Wiki 配置加载完成 -> enabled=%s, api_url=%s",
            cfg.get("enabled"),
            cfg.get("api_url"),
        )
    return cfg
