from __future__ import annotations

import logging
from typing import Any

from core.config_loader import load_plugin_config

logger = logging.getLogger("HikariBot.Sts2WikiConfig")

DEFAULT_STS2_WIKI_CONFIG: dict[str, Any] = {
    "enabled": True,
    "api_url": "https://slaythespire.wiki.gg/api.php",
    "cache_ttl_seconds": 86400,
    "timeout": 10,
    "search_limit": 5,
    "summary_max_chars": 300,
    "query_max_chars": 80,
    "max_cache_entries": 500,
    "proxy": "",
    "user_agent": "HikariBot/1.0 SlayTheSpire2WikiQuery",
}

_first_load_done = False


def get_config() -> dict[str, Any]:
    global _first_load_done
    cfg = load_plugin_config("sts2_wiki", DEFAULT_STS2_WIKI_CONFIG)
    if not _first_load_done:
        _first_load_done = True
        logger.info(
            "杀戮尖塔 2 Wiki 配置加载完成 -> enabled=%s, api_url=%s, cache_ttl_seconds=%s",
            cfg.get("enabled"),
            cfg.get("api_url"),
            cfg.get("cache_ttl_seconds"),
        )
    return cfg
