"""
Cobalt 插件配置加载模块。

从 BotData/plugin_configs/cobalt_parser.json 读取配置。
"""

import logging
from typing import Any

from core.config_loader import load_plugin_config, DEFAULT_COBALT_CONFIG

logger = logging.getLogger("HikariBot.CobaltConfig")

_cobalt_config: dict[str, Any] | None = None


def load_config() -> dict[str, Any]:
    """加载 Cobalt 插件配置。"""
    global _cobalt_config
    if _cobalt_config is None:
        _cobalt_config = load_plugin_config("cobalt_parser", DEFAULT_COBALT_CONFIG)
        _log_config_summary()
    return _cobalt_config


def get_config() -> dict[str, Any]:
    """获取当前配置（必须已加载）。"""
    if _cobalt_config is None:
        return load_config()
    return _cobalt_config


def _log_config_summary() -> None:
    """输出配置摘要到日志。"""
    cfg = get_config()
    send_strategy = cfg.get("send_strategy", {})
    logger.info(
        f"Cobalt 配置加载完成 → "
        f"auto_parse={cfg.get('auto_parse')}, "
        f"cobalt_api={cfg.get('cobalt_api')}, "
        f"max_send={cfg.get('max_send')}, "
        f"api_key={'已配置' if cfg.get('api_key') else '未配置'}, "
        f"prefer_forward={send_strategy.get('prefer_forward_message')}, "
        f"fallback_separate={send_strategy.get('fallback_to_separate_media')}"
    )
