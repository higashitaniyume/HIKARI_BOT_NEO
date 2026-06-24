from __future__ import annotations

import logging
from typing import Any

from core.config_loader import load_plugin_config

logger = logging.getLogger("HikariBot.OsuConfig")

DEFAULT_OSU_CONFIG: dict[str, Any] = {
    "enabled": True,
    "client_id": "",
    "client_secret": "",
    "api_base": "https://osu.ppy.sh/api/v2",
    "oauth_url": "https://osu.ppy.sh/oauth/token",
    "default_mode": "osu",
    "timeout": 20,
    "proxy": "",
    "cache_dir": "/tmp/hikari_bot/osu_info",
    "ranking_limit": 10,
    "score_limit": 5,
    "beatmap_search_limit": 5,
}

_first_load_done = False


def get_config() -> dict[str, Any]:
    global _first_load_done
    cfg = load_plugin_config("osu_info", DEFAULT_OSU_CONFIG)
    if not _first_load_done:
        _first_load_done = True
        logger.info(
            "osu! 配置加载完成 -> enabled=%s, client_id=%s, default_mode=%s",
            cfg.get("enabled"),
            "已配置" if cfg.get("client_id") else "未配置",
            cfg.get("default_mode"),
        )
    return cfg
