"""Media detail web configuration."""

from __future__ import annotations

import logging
from typing import Any

from core.config_loader import DEFAULT_MEDIA_DETAIL_WEB_CONFIG, load_plugin_config

logger = logging.getLogger("HikariBot.MediaDetailWebConfig")

_first_load_done = False


def get_config() -> dict[str, Any]:
    """Load the web page config with hot-reload support."""
    global _first_load_done
    cfg = load_plugin_config("media_detail_web", DEFAULT_MEDIA_DETAIL_WEB_CONFIG)
    if not _first_load_done:
        _first_load_done = True
        logger.info(
            "媒体详情 Web 配置加载完成 -> enabled=%s, host=%s, port=%s, auto_download=%s",
            cfg.get("enabled"),
            cfg.get("host"),
            cfg.get("port"),
            cfg.get("auto_download"),
        )
    return cfg
