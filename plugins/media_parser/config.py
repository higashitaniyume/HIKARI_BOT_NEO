"""Aggregated media parser configuration."""

from __future__ import annotations

import logging
from typing import Any

from core.config_loader import DEFAULT_MEDIA_PARSER_CONFIG, load_plugin_config

logger = logging.getLogger("HikariBot.MediaParserConfig")

_first_load_done = False


def get_config() -> dict[str, Any]:
    """Load the media parser config with hot-reload support."""
    global _first_load_done
    cfg = load_plugin_config("media_parser", DEFAULT_MEDIA_PARSER_CONFIG)
    if not _first_load_done:
        _first_load_done = True
        _log_config_summary(cfg)
    return cfg


def _log_config_summary(cfg: dict[str, Any]) -> None:
    parsers = cfg.get("parsers", {})
    enabled = [
        name for name, mode in parsers.items()
        if str(mode or "").strip() != "关闭"
    ]
    logger.info(
        "Media parser config loaded -> enabled=%s, auto_parse=%s, cache_ttl_seconds=%s, parsers=%s",
        cfg.get("enabled"),
        (cfg.get("trigger") or {}).get("auto_parse"),
        (cfg.get("download") or {}).get("cache_ttl_seconds"),
        ",".join(enabled) or "none",
    )
