from __future__ import annotations

import logging
from typing import Any

from core.config_loader import load_plugin_config

logger = logging.getLogger("HikariBot.MusicGenreWikiConfig")

DEFAULT_MUSIC_GENRE_CONFIG: dict[str, Any] = {
    "enabled": True,
    "fuzzy_match": True,
    "max_results": 5,
    "detail_max_chars": 1500,
    "data_file": "plugins/music_genre_wiki/data.json",
}

_first_load_done = False


def get_config() -> dict[str, Any]:
    global _first_load_done
    cfg = load_plugin_config("music_genre_wiki", DEFAULT_MUSIC_GENRE_CONFIG)
    if not _first_load_done:
        _first_load_done = True
        logger.info(
            "音乐流派 Wiki 配置加载完成 -> enabled=%s",
            cfg.get("enabled"),
        )
    return cfg
