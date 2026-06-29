"""
YouTube 下载插件配置加载模块。

从 BotData/plugin_configs/youtube_downloader.json 读取配置。
"""

from __future__ import annotations

import logging
from typing import Any

from core.config_loader import DEFAULT_YOUTUBE_DOWNLOADER_CONFIG, load_plugin_config

logger = logging.getLogger("HikariBot.YouTubeConfig")

_first_load_done = False


def get_config() -> dict[str, Any]:
    """获取 YouTube 下载插件当前配置。"""
    global _first_load_done
    cfg = load_plugin_config("youtube_downloader", DEFAULT_YOUTUBE_DOWNLOADER_CONFIG)
    if not _first_load_done:
        _first_load_done = True
        _log_config_summary(cfg)
    return cfg


def _log_config_summary(cfg: dict[str, Any]) -> None:
    """首次加载时输出配置摘要到日志。"""
    logger.info(
        "YouTube 下载配置加载完成 -> "
        "enabled=%s, auto_parse=%s, max_links_per_message=%s, "
        "max_file_mb=%s, max_height=%s, cache_dir=%s, cookiefile=%s",
        cfg.get("enabled"),
        cfg.get("auto_parse"),
        cfg.get("max_links_per_message"),
        cfg.get("max_file_mb"),
        cfg.get("max_height"),
        cfg.get("cache_dir"),
        "已配置" if cfg.get("cookiefile") else "未配置",
    )
