"""Aggregated media parser configuration."""

from __future__ import annotations

import logging
from typing import Any

from core.config_loader import DEFAULT_MEDIA_PARSER_CONFIG, load_plugin_config

logger = logging.getLogger("HikariBot.MediaParserConfig")

# `parsers.<平台>` 除上游的 关闭 / 全部发送 / 仅文本 / 仅富媒体 外，本地额外支持
# 「仅视频」：只发送该链接解析出的视频，不发送图片；游戏信息等文本随合并转发首条一起
# 发出，不单独发文本消息。
# 上游把不认识的模式当成「关闭」（`_parser_enabled` → `controller_has_any_output`），
# 所以进入上游前必须先归一化成「全部发送」（开文本、开富媒体），再由本地发送链按
# VIDEO_ONLY_PLATFORMS_KEY 把图片过滤掉。
OUTPUT_MODE_VIDEO_ONLY = "仅视频"
UPSTREAM_MODE_FULL = "全部发送"
VIDEO_ONLY_PLATFORMS_KEY = "_video_only_platforms"

_first_load_done = False


def get_config() -> dict[str, Any]:
    """Load the media parser config with hot-reload support."""
    global _first_load_done
    cfg = load_plugin_config("media_parser", DEFAULT_MEDIA_PARSER_CONFIG)
    if not _first_load_done:
        _first_load_done = True
        _log_config_summary(cfg)
    return normalize_output_modes(cfg)


def normalize_output_modes(cfg: dict[str, Any]) -> dict[str, Any]:
    """把「仅视频」归一化成上游认得的模式，并记录仅视频平台。

    幂等：只有 `parsers` 里还存在原始的「仅视频」时才改写，因此 `get_config()` 与
    `create_runtime()` 都可以安全调用。就地更新 `cfg`（`parsers` 会换成新字典，
    不会改到调用方传进来的嵌套字典），需要保护原字典时先传浅拷贝。
    """
    parsers = cfg.get("parsers")
    if not isinstance(parsers, dict):
        return cfg

    video_only = {
        str(name)
        for name, mode in parsers.items()
        if str(mode or "").strip() == OUTPUT_MODE_VIDEO_ONLY
    }
    if not video_only:
        return cfg

    cfg["parsers"] = {
        name: (UPSTREAM_MODE_FULL if str(name) in video_only else mode)
        for name, mode in parsers.items()
    }
    cfg[VIDEO_ONLY_PLATFORMS_KEY] = sorted(video_only)
    return cfg


def video_only_platforms(cfg: dict[str, Any]) -> set[str]:
    """返回配置成「仅视频」的平台名集合。"""
    platforms = cfg.get(VIDEO_ONLY_PLATFORMS_KEY) or []
    if isinstance(platforms, str):
        platforms = [platforms]
    return {str(name).strip() for name in platforms if str(name or "").strip()}


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
