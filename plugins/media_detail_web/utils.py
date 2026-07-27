"""Shared utility functions for media detail web service."""

from __future__ import annotations

import logging
from typing import Any

from third_party.astrbot_plugin_media_parser.core.downloader.utils import strip_media_prefixes

logger = logging.getLogger("HikariBot.MediaDetailWeb")


class _LinkBudget:
    def __init__(self, limit: int) -> None:
        self.limit = max(1, int(limit))
        self.used = 0
        self.dropped = 0

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit

    def take(self, values: list[Any]) -> list[Any]:
        allowed = max(0, self.limit - self.used)
        selected = values[:allowed]
        self.used += len(selected)
        self.dropped += max(0, len(values) - len(selected))
        return selected


def _suppress_redundant_error_metadata(metadata_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    successes = [item for item in metadata_list if not item.get("error")]
    failures = [item for item in metadata_list if item.get("error")]
    if not successes or not failures:
        return metadata_list
    for item in failures:
        logger.info(
            "[MediaDetailWeb] suppress failed candidate because another candidate succeeded -> platform=%s url=%s error=%s",
            item.get("platform") or item.get("parser_name") or "unknown",
            item.get("source_url") or item.get("url") or "",
            item.get("error"),
        )
    return successes


def _normalize_url_groups(value: Any) -> list[list[str]]:
    if not isinstance(value, list):
        return []
    groups: list[list[str]] = []
    for item in value:
        if isinstance(item, list):
            group = [strip_media_prefixes(str(url)) for url in item if str(url or "").strip()]
            if group:
                groups.append(group)
        elif str(item or "").strip():
            groups.append([strip_media_prefixes(str(item))])
    return groups


def _first_url(urls: list[str]) -> str:
    for url in urls:
        stripped = strip_media_prefixes(str(url or ""))
        if stripped:
            return stripped
    return ""


def _metadata_flags(metadata: dict[str, Any]) -> list[str]:
    flags = []
    for key in ("restriction_label", "access_status", "access_message"):
        value = str(metadata.get(key) or "").strip()
        if value and value not in flags:
            flags.append(value)
    if metadata.get("video_cover_only"):
        flags.append("视频封面模式")
    if metadata.get("has_access_denied"):
        flags.append("访问受限")
    if metadata.get("exceeds_max_size"):
        flags.append("超过大小限制")
    return flags


def _metadata_details(metadata: dict[str, Any], video_count: int, image_count: int) -> list[dict[str, str]]:
    details = [
        {"label": "视频数量", "value": str(video_count)},
        {"label": "图片数量", "value": str(image_count)},
    ]
    if metadata.get("total_video_size_mb"):
        details.append({"label": "视频合计", "value": f"{float(metadata['total_video_size_mb']):.1f}MB"})
    if metadata.get("failed_video_count") or metadata.get("failed_image_count"):
        details.append({
            "label": "跳过媒体",
            "value": f"视频 {metadata.get('failed_video_count', 0)} / 图片 {metadata.get('failed_image_count', 0)}",
        })
    if metadata.get("hot_comments"):
        details.append({"label": "热评", "value": str(len(metadata.get("hot_comments") or []))})
    return details


def _error_item(*, platform: str, source: str, source_url: str, error: str) -> dict[str, Any]:
    return {
        "source": source,
        "platform": platform,
        "source_url": source_url,
        "title": "",
        "author": "",
        "description": "",
        "timestamp": "",
        "tags": [],
        "flags": [],
        "details": [],
        "summary": {"videos": 0, "images": 0, "downloaded": 0},
        "media": [],
        "warnings": [],
        "error": error,
    }


def _skipped_media(kind: str, label: str, reason: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "label": label,
        "filename": "",
        "content_type": "",
        "size_bytes": None,
        "mode": "skip",
        "preview_url": "",
        "download_url": "",
        "source_url": "",
        "status": "skipped",
        "skip_reason": reason,
    }


def _max_proxy_bytes(web_cfg: dict[str, Any]) -> int:
    return max(1, int(web_cfg.get("max_remote_proxy_mb", 1024))) * 1024 * 1024


def _string_headers(headers: dict[str, Any]) -> dict[str, str]:
    return {str(k): str(v) for k, v in headers.items() if v is not None}


def _format_duration(seconds: int) -> str:
    if seconds <= 0:
        return "未知"
    minutes, sec = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minute:02d}:{sec:02d}"
    return f"{minute}:{sec:02d}"


def _format_size(size: int | None) -> str:
    if not size:
        return "未知"
    mb = size / 1024 / 1024
    if mb >= 1024:
        return f"{mb / 1024:.2f}GB"
    return f"{mb:.1f}MB"


def _format_number(value: Any) -> str:
    if value is None:
        return "未知"
    try:
        return f"{int(value):,}"
    except Exception:
        return str(value)


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value:
            return value
    return ""
