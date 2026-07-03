"""
Pixiv 插件配置加载模块。

从 BotData/plugin_configs/pixiv_parser.json 读取配置。
支持热重载：每次调用 get_config() 都从磁盘重新读取。
"""

import logging
from typing import Any

from core.config_loader import load_plugin_config, DEFAULT_PIXIV_CONFIG

logger = logging.getLogger("HikariBot.PixivConfig")

_first_load_done = False


def get_config() -> dict[str, Any]:
    """
    获取 Pixiv 插件当前配置（每次调用都从磁盘重新读取，支持热重载）。

    如果配置文件不存在，自动创建默认配置。
    """
    global _first_load_done
    cfg = load_plugin_config("pixiv_parser", DEFAULT_PIXIV_CONFIG)
    if not _first_load_done:
        _first_load_done = True
        _log_config_summary(cfg)
    return cfg


def _log_config_summary(cfg: dict[str, Any]) -> None:
    """首次加载时输出配置摘要到日志。"""
    send_strategy = cfg.get("send_strategy", {})
    logger.info(
        f"Pixiv 配置加载完成 → "
        f"auto_parse={cfg.get('auto_parse')}, "
        f"max_send={cfg.get('max_send')}, "
        f"max_file_mb={cfg.get('max_file_mb')}, "
        f"allow_r18={cfg.get('allow_r18')}, "
        f"send_link_info={cfg.get('send_link_info')}, "
        f"cache_ttl_seconds={cfg.get('cache_ttl_seconds')}, "
        f"proxy={'已配置' if cfg.get('proxy') else '未配置'}, "
        f"cookie={'已配置' if cfg.get('cookie') else '未配置'}, "
        f"cache_dir={cfg.get('cache_dir')}, "
        f"prefer_forward={send_strategy.get('prefer_forward_message')}, "
        f"fallback_separate={send_strategy.get('fallback_to_separate_images')}"
    )
