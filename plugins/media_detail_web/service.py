"""Unified media parsing service for the standalone detail web page."""

from __future__ import annotations

import logging
from typing import Any

from .config import get_config
from .registry import cleanup_registry
from .utils import _LinkBudget

from .pixiv_handler import _parse_pixiv_links
from .youtube_handler import _parse_youtube_links
from .cobalt_handler import _parse_cobalt_links
from .aggregated_handler import _parse_aggregated_links

logger = logging.getLogger("HikariBot.MediaDetailWeb")

SUPPORTED_PLATFORM_GROUPS = [
    {
        "name": "聚合媒体解析",
        "platforms": [
            "Bilibili",
            "抖音",
            "TikTok",
            "快手",
            "微博",
            "小红书",
            "闲鱼",
            "今日头条",
            "小黑盒",
            "Twitter/X",
        ],
    },
    {"name": "Pixiv", "platforms": ["Pixiv artworks"]},
    {"name": "YouTube", "platforms": ["YouTube", "YouTube Shorts", "youtu.be"]},
    {"name": "Cobalt", "platforms": ["Instagram", "Facebook"]},
]


async def parse_media_text(text: str, *, download: bool | None = None) -> dict[str, Any]:
    """Parse media links in text and return a page-friendly JSON payload."""
    cfg = get_config()
    limit = max(1, int(cfg.get("max_links_per_request", 8)))
    auto_download = bool(cfg.get("auto_download", True)) if download is None else bool(download)
    ttl_seconds = max(60, int(cfg.get("token_ttl_seconds", 3600)))
    max_entries = max(1, int(cfg.get("max_registry_entries", 512)))
    cleanup_registry(max_entries=max_entries)

    text = str(text or "").strip()
    if not text:
        return {
            "items": [],
            "messages": ["请输入要解析的 URL。"],
            "download_enabled": auto_download,
            "platform_groups": SUPPORTED_PLATFORM_GROUPS,
        }

    remaining = _LinkBudget(limit)
    items: list[dict[str, Any]] = []
    messages: list[str] = []

    for parser in (
        _parse_pixiv_links,
        _parse_youtube_links,
        _parse_cobalt_links,
        _parse_aggregated_links,
    ):
        if remaining.exhausted:
            break
        try:
            parsed = await parser(text, auto_download, remaining, cfg, ttl_seconds)
            items.extend(parsed)
        except Exception as e:
            logger.exception("[MediaDetailWeb] parser group failed: %s", e)
            messages.append(f"部分解析器执行失败：{e}")

    if remaining.dropped > 0:
        messages.append(f"已达到单次解析上限，跳过 {remaining.dropped} 个链接。")
    if not items:
        messages.append("没有找到当前机器人媒体解析插件支持的链接。")

    return {
        "items": items,
        "messages": messages,
        "download_enabled": auto_download,
        "platform_groups": SUPPORTED_PLATFORM_GROUPS,
    }
