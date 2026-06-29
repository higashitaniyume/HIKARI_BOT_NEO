"""
Cobalt 插件配置加载模块。

从 BotData/plugin_configs/cobalt_parser.json 读取配置。
支持热重载：每次调用 get_config() 都从磁盘重新读取。
"""

import logging
from typing import Any

from core.config_loader import load_plugin_config, DEFAULT_COBALT_CONFIG

logger = logging.getLogger("HikariBot.CobaltConfig")

_first_load_done = False


def get_config() -> dict[str, Any]:
    """
    获取 Cobalt 插件当前配置（每次调用都从磁盘重新读取，支持热重载）。

    如果配置文件不存在，自动创建默认配置。
    """
    global _first_load_done
    cfg = load_plugin_config("cobalt_parser", DEFAULT_COBALT_CONFIG)
    if not _first_load_done:
        _first_load_done = True
        _log_config_summary(cfg)
    return cfg


def _log_config_summary(cfg: dict[str, Any]) -> None:
    """首次加载时输出配置摘要到日志。"""
    send_strategy = cfg.get("send_strategy", {})
    logger.info(
        f"Cobalt 配置加载完成 → "
        f"auto_parse={cfg.get('auto_parse')}, "
        f"cobalt_api={cfg.get('cobalt_api')}, "
        f"max_send={cfg.get('max_send')}, "
        f"send_link_info={cfg.get('send_link_info')}, "
        f"parse_retry_count={cfg.get('parse_retry_count')}, "
        f"parse_retry_delay_seconds={cfg.get('parse_retry_delay_seconds')}, "
        f"api_key={'已配置' if cfg.get('api_key') else '未配置'}, "
        f"prefer_forward={send_strategy.get('prefer_forward_message')}, "
        f"fallback_separate={send_strategy.get('fallback_to_separate_media')}"
    )
