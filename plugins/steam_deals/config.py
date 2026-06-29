from __future__ import annotations

import logging
from typing import Any

from core.config_loader import load_plugin_config

logger = logging.getLogger("HikariBot.SteamDealsConfig")

DEFAULT_STEAM_DEALS_CONFIG: dict[str, Any] = {
    "enabled": True,
    "api_url": "https://store.steampowered.com/api/featuredcategories",
    "country": "cn",
    "language": "schinese",
    "currency_symbol": "¥",
    "timeout": 20,
    "proxy": "",
    "cache_dir": "/tmp/hikari_bot/steam_deals",
    "cache_ttl_minutes": 30,
    "max_items": 12,
    "max_low_price_cents": 1000,
    "min_discount_percent": 90,
    "schedule": {
        "enabled": False,
        "time": "10:00",
        "timezone": "Asia/Shanghai",
        "startup_delay_seconds": 30,
        "check_interval_seconds": 60,
    },
    "push_whitelist": {
        "group_ids": [],
        "private_user_ids": [],
    },
    "render": {
        "download_covers": True,
        "cover_timeout": 10,
    },
}

_first_load_done = False


def get_config() -> dict[str, Any]:
    global _first_load_done
    cfg = load_plugin_config("steam_deals", DEFAULT_STEAM_DEALS_CONFIG)
    if not _first_load_done:
        _first_load_done = True
        whitelist = cfg.get("push_whitelist") or {}
        logger.info(
            "Steam 喜加一日报配置加载完成 -> enabled=%s, schedule=%s, groups=%s, privates=%s",
            cfg.get("enabled"),
            (cfg.get("schedule") or {}).get("enabled"),
            len(whitelist.get("group_ids") or []),
            len(whitelist.get("private_user_ids") or []),
        )
    return cfg
