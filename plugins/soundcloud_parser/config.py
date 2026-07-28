"""
SoundCloud 解析器配置加载模块。
"""

from __future__ import annotations

import logging
from typing import Any

from core.config_loader import DEFAULT_SOUNDCLOUD_PARSER_CONFIG, load_plugin_config

logger = logging.getLogger("HikariBot.SoundCloudPlugin")

_first_load_done = False


def get_config() -> dict[str, Any]:
    """获取 SoundCloud 解析器配置（支持热重载）。"""
    global _first_load_done
    cfg = load_plugin_config("soundcloud_parser", DEFAULT_SOUNDCLOUD_PARSER_CONFIG)
    if not _first_load_done:
        _first_load_done = True
        _log_config_summary(cfg)
    return cfg


def _log_config_summary(cfg: dict[str, Any]) -> None:
    """首次加载时输出配置摘要。"""
    if cfg.get("enabled", True):
        logger.info("[SoundCloud] ✓ 已启用 | auto_parse=%s", cfg.get("auto_parse", True))
    else:
        logger.info("[SoundCloud] ✗ 已禁用")
