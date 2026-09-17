"""
QQ 音乐解析插件配置加载模块。

从 ``BotData/plugin_configs/qqmusic_parser.json`` 读取配置，并负责把配置里的
``cookiefile`` 解析成绝对路径。

``cookiefile`` 支持相对路径（相对仓库根目录）与绝对路径：容器里工作目录是
``/app``，本地开发是仓库根目录，按仓库根解析两种环境都成立。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from core.config_loader import DEFAULT_QQMUSIC_CONFIG, load_plugin_config

logger = logging.getLogger("HikariBot.QQMusicConfig")

# plugins/qqmusic_parser/config.py -> 仓库根目录
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 默认 cookie 位置（相对仓库根）。真实文件不进 git，见 .gitignore。
DEFAULT_COOKIE_RELATIVE_PATH = "BotData/cookies/qqmusic.txt"

_first_load_done = False


def get_config() -> dict[str, Any]:
    """获取 QQ 音乐解析插件当前配置（每次调用都从磁盘重新读取，支持热重载）。"""
    global _first_load_done
    cfg = load_plugin_config("qqmusic_parser", DEFAULT_QQMUSIC_CONFIG, force_reload=True)
    if not _first_load_done:
        _first_load_done = True
        _log_config_summary(cfg)
    return cfg


def resolve_cookiefile(raw: Any) -> Path | None:
    """把配置里的 cookiefile 解析成绝对路径；未配置时返回 None。"""
    text = str(raw or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def get_cookiefile(cfg: dict[str, Any] | None = None) -> Path | None:
    """读取当前配置中的 cookie 文件路径。"""
    current = cfg if cfg is not None else get_config()
    return resolve_cookiefile(current.get("cookiefile"))


def cookiefile_display_path(cfg: dict[str, Any] | None = None) -> str:
    """给用户看的 cookie 路径（配置值，未配置时给默认值）。"""
    current = cfg if cfg is not None else get_config()
    text = str(current.get("cookiefile") or "").strip()
    return text or DEFAULT_COOKIE_RELATIVE_PATH


def _log_config_summary(cfg: dict[str, Any]) -> None:
    """首次加载时输出配置摘要到日志。"""
    cookie_path = get_cookiefile(cfg)
    cookie_state = "未配置"
    if cookie_path is not None:
        cookie_state = f"已配置({cookie_path})" if cookie_path.is_file() else f"文件不存在({cookie_path})"
    logger.info(
        "QQ 音乐解析配置加载完成 → "
        "enabled=%s, auto_parse=%s, max_links_per_message=%s, format_priority=%s, "
        "max_file_mb=%s, cookie=%s, cache_dir=%s, cache_ttl_seconds=%s",
        cfg.get("enabled"),
        cfg.get("auto_parse"),
        cfg.get("max_links_per_message"),
        cfg.get("format_priority"),
        cfg.get("max_file_mb"),
        cookie_state,
        cfg.get("cache_dir"),
        cfg.get("cache_ttl_seconds"),
    )
