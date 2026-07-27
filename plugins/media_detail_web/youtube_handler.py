"""YouTube media parsing handler."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError

from plugins.youtube_downloader.config import get_config as get_youtube_config
from plugins.youtube_downloader.downloader import (
    YouTubeDownloadError,
    _build_ydl_opts,
    _ensure_deno_on_path,
    download_youtube_video,
)
from plugins.youtube_downloader.parser import extract_youtube_urls

from .config import get_config
from .registry import register_file, register_remote
from .utils import (
    _error_item,
    _first_non_empty,
    _format_duration,
    _format_number,
    _format_size,
    _LinkBudget,
    _max_proxy_bytes,
    _skipped_media,
)

logger = logging.getLogger("HikariBot.MediaDetailWeb")


async def _parse_youtube_links(
    text: str,
    download: bool,
    budget: _LinkBudget,
    web_cfg: dict[str, Any],
    ttl_seconds: int,
) -> list[dict[str, Any]]:
    urls = budget.take(extract_youtube_urls(text))
    if not urls:
        return []

    cfg = get_youtube_config()
    if not cfg.get("enabled", True):
        return [
            _error_item(
                platform="YouTube",
                source="youtube_downloader",
                source_url=url,
                error="YouTube 下载插件已关闭。",
            )
            for url in urls
        ]

    max_proxy_bytes = _max_proxy_bytes(web_cfg)
    items: list[dict[str, Any]] = []
    for url in urls:
        try:
            if download:
                result = await download_youtube_video(url, cfg)
                item = _youtube_result_item(result)
                media = register_file(
                    result.path,
                    kind="video",
                    ttl_seconds=ttl_seconds,
                    filename=result.path.name,
                    source_url=result.webpage_url,
                )
                media["label"] = "视频"
                item["media"].append(media)
                item["summary"]["downloaded"] = 1
                items.append(item)
            else:
                info = await _extract_youtube_info(url, cfg)
                item = _youtube_info_item(info, source_url=url)
                thumb = _first_non_empty(info.get("thumbnail"), *(info.get("thumbnails") or []))
                if isinstance(thumb, dict):
                    thumb = thumb.get("url", "")
                if thumb:
                    media = register_remote(
                        str(thumb),
                        kind="image",
                        ttl_seconds=ttl_seconds,
                        filename=f"youtube_{info.get('id') or 'thumbnail'}.jpg",
                        max_proxy_bytes=max_proxy_bytes,
                        source_url=str(thumb),
                    )
                    media["label"] = "封面"
                    item["media"].append(media)
                item["warnings"].append("未启用自动下载，页面仅展示 YouTube 元信息和封面。")
                items.append(item)
        except YouTubeDownloadError as e:
            logger.warning("[MediaDetailWeb] YouTube download failed: %s", e)
            items.append(_error_item(
                platform="YouTube",
                source="youtube_downloader",
                source_url=url,
                error=str(e),
            ))
        except Exception as e:
            logger.exception("[MediaDetailWeb] YouTube parse failed: %s", e)
            items.append(_error_item(
                platform="YouTube",
                source="youtube_downloader",
                source_url=url,
                error=str(e),
            ))
    return items


async def _extract_youtube_info(url: str, cfg: dict[str, Any]) -> dict[str, Any]:
    return await asyncio.to_thread(_extract_youtube_info_sync, url, cfg)


def _extract_youtube_info_sync(url: str, cfg: dict[str, Any]) -> dict[str, Any]:
    _ensure_deno_on_path()
    max_file_mb = max(1, int(cfg.get("max_file_mb", 1024)))
    max_height = max(144, int(cfg.get("max_height", 720)))
    socket_timeout = max(5, int(cfg.get("socket_timeout", 30)))
    retries = max(0, int(cfg.get("retries", 5)))
    opts = _build_ydl_opts(
        cfg,
        download=False,
        max_bytes=max_file_mb * 1024 * 1024,
        max_height=max_height,
        socket_timeout=socket_timeout,
        retries=retries,
    )
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except (DownloadError, ExtractorError) as e:
        raise RuntimeError(str(e)) from e
    if not isinstance(info, dict):
        raise RuntimeError("无法读取视频信息。")
    if info.get("_type") == "playlist":
        raise RuntimeError("暂不支持播放列表，请输入单个视频链接。")
    return info


def _youtube_result_item(result: Any) -> dict[str, Any]:
    return {
        "source": "youtube_downloader",
        "platform": "YouTube",
        "source_url": result.webpage_url,
        "title": result.title,
        "author": result.uploader,
        "description": "",
        "timestamp": "",
        "tags": [],
        "flags": [],
        "details": [
            {"label": "视频 ID", "value": result.video_id},
            {"label": "时长", "value": _format_duration(result.duration)},
            {"label": "文件大小", "value": _format_size(result.filesize)},
        ],
        "summary": {"videos": 1, "images": 0, "downloaded": 0},
        "media": [],
        "warnings": [],
        "error": "",
    }


def _youtube_info_item(info: dict[str, Any], *, source_url: str) -> dict[str, Any]:
    title = str(info.get("title") or "YouTube Video")
    uploader = str(info.get("uploader") or info.get("channel") or "Unknown")
    webpage_url = str(info.get("webpage_url") or source_url)
    duration = int(info.get("duration") or 0)
    return {
        "source": "youtube_downloader",
        "platform": "YouTube",
        "source_url": webpage_url,
        "title": title,
        "author": uploader,
        "description": str(info.get("description") or ""),
        "timestamp": str(info.get("upload_date") or ""),
        "tags": [str(tag) for tag in (info.get("tags") or [])[:12]],
        "flags": [],
        "details": [
            {"label": "视频 ID", "value": str(info.get("id") or "")},
            {"label": "时长", "value": _format_duration(duration)},
            {"label": "观看数", "value": _format_number(info.get("view_count"))},
        ],
        "summary": {"videos": 1, "images": 0, "downloaded": 0},
        "media": [],
        "warnings": [],
        "error": "",
    }
