"""
网易云音乐解析插件配置加载模块。

从 BotData/plugin_configs/netease_parser.json 读取配置。
支持热重载：每次调用 get_config() 都从磁盘重新读取。
"""

import logging
from typing import Any

from core.config_loader import DEFAULT_NETEASE_CONFIG, load_plugin_config

logger = logging.getLogger("HikariBot.NeteaseConfig")

_first_load_done = False


def get_config() -> dict[str, Any]:
    """
    获取网易云解析插件当前配置（每次调用都从磁盘重新读取，支持热重载）。

    如果配置文件不存在，自动创建默认配置。
    """
    global _first_load_done
    cfg = load_plugin_config("netease_parser", DEFAULT_NETEASE_CONFIG)
    if not _first_load_done:
        _first_load_done = True
        _log_config_summary(cfg)
    return cfg


def _log_config_summary(cfg: dict[str, Any]) -> None:
    """首次加载时输出配置摘要到日志。"""
    logger.info(
        "网易云解析配置加载完成 → "
        "auto_parse=%s, api_base_url=%s, "
        "max_file_mb=%s, cache_ttl_seconds=%s",
        cfg.get("auto_parse"),
        cfg.get("api_base_url"),
        cfg.get("max_file_mb"),
        cfg.get("cache_ttl_seconds"),
    )
