"""Aggregated multi-platform media parsing handler (Bilibili, Douyin, TikTok, etc.)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import aiohttp

from plugins.media_parser.cache_cleanup import media_cache_ttl_seconds, register_metadata_temp_media
from plugins.media_parser.config import get_config as get_media_parser_config
from plugins.media_parser.runtime import create_runtime

from .registry import register_file, register_remote
from .utils import (
    _first_url,
    _LinkBudget,
    _max_proxy_bytes,
    _metadata_details,
    _metadata_flags,
    _normalize_url_groups,
    _skipped_media,
    _string_headers,
    _suppress_redundant_error_metadata,
)

logger = logging.getLogger("HikariBot.MediaDetailWeb")


async def _parse_aggregated_links(
    text: str,
    download: bool,
    budget: _LinkBudget,
    web_cfg: dict[str, Any],
    ttl_seconds: int,
) -> list[dict[str, Any]]:
    cfg = get_media_parser_config()
    if not cfg.get("enabled", True) or budget.exhausted:
        return []

    try:
        runtime = create_runtime(cfg)
    except Exception as e:
        logger.warning("[MediaDetailWeb] media parser runtime unavailable: %s", e)
        return []

    links = budget.take(runtime.parser_manager.extract_all_links(text))
    if not links:
        return []

    timeout = aiohttp.ClientTimeout(total=max(30, int(cfg.get("api_timeout", 120))))
    max_proxy_bytes = _max_proxy_bytes(web_cfg)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        metadata_list = await runtime.parser_manager.parse_text(
            text,
            session,
            links_with_parser=links,
        )
        metadata_list = _suppress_redundant_error_metadata(metadata_list)
        items: list[dict[str, Any]] = []
        cache_ttl_seconds = media_cache_ttl_seconds(cfg)
        for metadata in metadata_list:
            if download and not metadata.get("error"):
                metadata = await runtime.download_manager.process_metadata(
                    session=session,
                    metadata=metadata,
                    proxy_addr=runtime.config_manager.proxy.address or None,
                )
                register_metadata_temp_media(metadata, ttl_seconds=cache_ttl_seconds)
            items.append(_aggregated_metadata_item(metadata, ttl_seconds, max_proxy_bytes))
        return items


def _aggregated_metadata_item(
    metadata: dict[str, Any],
    ttl_seconds: int,
    max_proxy_bytes: int,
) -> dict[str, Any]:
    platform = str(metadata.get("platform") or metadata.get("parser_name") or "unknown")
    source_url = str(metadata.get("source_url") or metadata.get("url") or "")
    video_urls = _normalize_url_groups(metadata.get("video_urls"))
    image_urls = _normalize_url_groups(metadata.get("image_urls"))
    video_count = len(video_urls)
    image_count = len(image_urls)

    item = {
        "source": "media_parser",
        "platform": platform,
        "source_url": source_url,
        "title": str(metadata.get("title") or ""),
        "author": str(metadata.get("author") or ""),
        "description": str(metadata.get("desc") or metadata.get("text") or ""),
        "timestamp": str(metadata.get("timestamp") or ""),
        "tags": [str(tag) for tag in (metadata.get("tags") or [])[:12]] if isinstance(metadata.get("tags"), list) else [],
        "flags": _metadata_flags(metadata),
        "details": _metadata_details(metadata, video_count, image_count),
        "summary": {
            "videos": video_count,
            "images": image_count,
            "downloaded": 0,
        },
        "media": [],
        "warnings": [],
        "error": str(metadata.get("error") or ""),
    }
    if metadata.get("error"):
        return item

    file_paths = metadata.get("file_paths") or []
    video_modes = metadata.get("video_modes") or ["direct"] * video_count
    image_modes = metadata.get("image_modes") or ["direct"] * image_count
    video_reasons = metadata.get("video_skip_reasons") or []
    image_reasons = metadata.get("image_skip_reasons") or []
    video_headers = _string_headers(metadata.get("video_headers") or {})
    image_headers = _string_headers(metadata.get("image_headers") or {})

    for index, urls in enumerate(video_urls):
        mode = str(video_modes[index]) if index < len(video_modes) else "direct"
        reason = str(video_reasons[index]) if index < len(video_reasons) and video_reasons[index] else ""
        media = _media_from_mode(
            kind="video",
            label=f"视频 {index + 1}",
            mode=mode,
            urls=urls,
            file_path=file_paths[index] if index < len(file_paths) else None,
            headers=video_headers,
            ttl_seconds=ttl_seconds,
            max_proxy_bytes=max_proxy_bytes,
            source_url=source_url,
            skip_reason=reason,
        )
        item["media"].append(media)

    for index, urls in enumerate(image_urls):
        mode = str(image_modes[index]) if index < len(image_modes) else "direct"
        reason = str(image_reasons[index]) if index < len(image_reasons) and image_reasons[index] else ""
        position = video_count + index
        media = _media_from_mode(
            kind="image",
            label=f"图片 {index + 1}",
            mode=mode,
            urls=urls,
            file_path=file_paths[position] if position < len(file_paths) else None,
            headers=image_headers,
            ttl_seconds=ttl_seconds,
            max_proxy_bytes=max_proxy_bytes,
            source_url=source_url,
            skip_reason=reason,
        )
        item["media"].append(media)

    item["summary"]["downloaded"] = sum(1 for media in item["media"] if media.get("status") != "skipped")
    skip_messages = [
        str(reason)
        for reason in (video_reasons + image_reasons)
        if reason
    ]
    if skip_messages:
        item["warnings"].extend(skip_messages[:4])
    return item


def _media_from_mode(
    *,
    kind: str,
    label: str,
    mode: str,
    urls: list[str],
    file_path: Any,
    headers: dict[str, str],
    ttl_seconds: int,
    max_proxy_bytes: int,
    source_url: str,
    skip_reason: str,
) -> dict[str, Any]:
    if mode == "local" and file_path:
        try:
            payload = register_file(
                Path(str(file_path)),
                kind=kind,
                ttl_seconds=ttl_seconds,
                source_url=_first_url(urls) or source_url,
            )
        except Exception as e:
            payload = _skipped_media(kind, label, str(e))
    elif mode == "direct" and urls:
        direct_url = _first_url(urls)
        if not direct_url:
            payload = _skipped_media(kind, label, skip_reason or "媒体直链为空。")
            payload["label"] = label
            return payload
        payload = register_remote(
            direct_url,
            kind=kind,
            ttl_seconds=ttl_seconds,
            headers=headers,
            max_proxy_bytes=max_proxy_bytes,
            source_url=direct_url or source_url,
        )
    else:
        payload = _skipped_media(kind, label, skip_reason or "媒体不可下载。")
    payload["label"] = label
    return payload
