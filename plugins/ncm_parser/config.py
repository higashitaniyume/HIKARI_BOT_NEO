"""
NCM 文件解密插件配置加载模块。

从 BotData/plugin_configs/ncm_parser.json 读取配置。
支持热重载：每次调用 get_config() 都从磁盘重新读取。
"""

import logging
from typing import Any

from core.config_loader import load_plugin_config

logger = logging.getLogger("HikariBot.NcmParserConfig")

_first_load_done = False

DEFAULT_NCM_CONFIG: dict[str, Any] = {
    "enabled": True,
    "auto_parse": True,
    "max_file_mb": 50,
    "temp_root": "/tmp/hikari_bot/ncm",
    "concurrency": 2,
    "retry_count": 2,
    "retry_delay_seconds": 2.0,
    "delete_after_send": True,
    "api_timeout": 60,
    "cache_ttl_seconds": 600,
    "send_link_info": True,
    "permissions": {
        "admin_id": "",
        "whitelist": {"enable": False, "user": [], "group": []},
        "blacklist": {"enable": False, "user": [], "group": []},
    },
}


def get_config() -> dict[str, Any]:
    """
    获取 NCM 解析插件当前配置（每次调用都从磁盘重新读取，支持热重载）。

    如果配置文件不存在，自动创建默认配置。
    """
    global _first_load_done
    cfg = load_plugin_config("ncm_parser", DEFAULT_NCM_CONFIG)
    if not _first_load_done:
        _first_load_done = True
        _log_config_summary(cfg)
    return cfg


def _log_config_summary(cfg: dict[str, Any]) -> None:
    """首次加载时输出配置摘要到日志。"""
    logger.info(
        "NCM 解析配置加载完成 → "
        "enabled=%s, auto_parse=%s, max_file_mb=%s, "
        "concurrency=%s, retry_count=%s, api_timeout=%s, "
        "delete_after_send=%s",
        cfg.get("enabled"),
        cfg.get("auto_parse"),
        cfg.get("max_file_mb"),
        cfg.get("concurrency"),
        cfg.get("retry_count"),
        cfg.get("api_timeout"),
        cfg.get("delete_after_send"),
    )
