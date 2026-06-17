"""
Pixiv 插件配置加载模块。

从 BotData/plugin_configs/pixiv_parser.json 读取配置。
"""

import logging
from typing import Any

from core.config_loader import load_plugin_config, DEFAULT_PIXIV_CONFIG

logger = logging.getLogger("HikariBot.PixivConfig")

_pixiv_config: dict[str, Any] | None = None


def load_config() -> dict[str, Any]:
    """
    加载 Pixiv 插件配置。

    如果配置文件不存在，自动创建默认配置。
    配置会被缓存，多次调用返回同一个对象。
    """
    global _pixiv_config
    if _pixiv_config is None:
        _pixiv_config = load_plugin_config("pixiv_parser", DEFAULT_PIXIV_CONFIG)
        _log_config_summary()
    return _pixiv_config


def reload_config() -> dict[str, Any]:
    """强制重新加载配置。"""
    global _pixiv_config
    _pixiv_config = None
    return load_config()


def get_config() -> dict[str, Any]:
    """获取当前配置（必须已加载）。"""
    if _pixiv_config is None:
        return load_config()
    return _pixiv_config


def _log_config_summary() -> None:
    """输出配置摘要到日志。"""
    cfg = get_config()
    send_strategy = cfg.get("send_strategy", {})
    logger.info(
        f"Pixiv 配置加载完成 → "
        f"auto_parse={cfg.get('auto_parse')}, "
        f"max_send={cfg.get('max_send')}, "
        f"max_file_mb={cfg.get('max_file_mb')}, "
        f"allow_r18={cfg.get('allow_r18')}, "
        f"proxy={'已配置' if cfg.get('proxy') else '未配置'}, "
        f"cookie={'已配置' if cfg.get('cookie') else '未配置'}, "
        f"cache_dir={cfg.get('cache_dir')}, "
        f"prefer_forward={send_strategy.get('prefer_forward_message')}, "
        f"fallback_separate={send_strategy.get('fallback_to_separate_images')}"
    )
