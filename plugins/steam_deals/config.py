from __future__ import annotations

import logging
from typing import Any

from core.config_loader import load_plugin_config

logger = logging.getLogger("HikariBot.SteamDealsConfig")

DEFAULT_STEAM_DEALS_CONFIG: dict[str, Any] = {
    "enabled": True,
    "api_url": "https://store.steampowered.com/api/featuredcategories",
    "search_url": "https://store.steampowered.com/search/results/",
    "steamdb_free_url": "https://steamdb.info/upcoming/free/",
    "country": "cn",
    "language": "schinese",
    "currency_symbol": "¥",
    "timeout": 20,
    "proxy": "",
    "cache_dir": "/tmp/hikari_bot/steam_deals",
    "cache_ttl_minutes": 30,
    "max_items": 18,
    "max_low_price_cents": 1000,
    "min_discount_percent": 90,
    "include_search_results": True,
    "search_pages": 2,
    "search_count_per_page": 50,
    "search_sort_by": ["Released_DESC"],
    "search_category1": 998,
    "include_steamdb_free_promotions": True,
    "daily_filter": {
        "enabled": True,
        "max_per_title_family": 2,
        "min_review_count_for_plain_low_price": 20,
        "min_discount_for_plain_low_price": 80,
        "min_discount_for_recent_deal": 20,
        "max_plain_low_price_items": 4,
        "require_recent_search_results": True,
        "max_search_release_age_days": 730,
    },
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
        "image_format": "JPEG",
        "jpeg_quality": 82,
    },
    "send_retry_attempts": 2,
    "send_retry_delay_seconds": 2.0,
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
