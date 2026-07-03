"""Unified media parsing service for the standalone detail web page."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import aiohttp
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError

from core.temp_media_cleaner import DEFAULT_TEMP_MEDIA_TTL_SECONDS, ttl_seconds_from_config
from plugins.cobalt_parser.config import get_config as get_cobalt_config
from plugins.cobalt_parser.downloader import download_media as download_cobalt_media
from plugins.cobalt_parser.parser import CobaltResult, call_cobalt_api, extract_social_urls
from plugins.media_parser.cache_cleanup import media_cache_ttl_seconds, register_metadata_temp_media
from plugins.media_parser.config import get_config as get_media_parser_config
from plugins.media_parser.runtime import create_runtime
from plugins.pixiv_parser.config import get_config as get_pixiv_config
from plugins.pixiv_parser.downloader import download_with_fallback, get_suffix_from_url
from plugins.pixiv_parser.parser import PixivArtwork, extract_pixiv_ids, fetch_artwork
from plugins.youtube_downloader.config import get_config as get_youtube_config
from plugins.youtube_downloader.downloader import (
    YouTubeDownloadError,
    _build_ydl_opts,
    _ensure_deno_on_path,
    download_youtube_video,
)
from plugins.youtube_downloader.parser import extract_youtube_urls
from third_party.astrbot_plugin_media_parser.core.downloader.utils import strip_media_prefixes

from .config import get_config
from .registry import cleanup_registry, register_file, register_remote

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


async def _parse_pixiv_links(
    text: str,
    download: bool,
    budget: _LinkBudget,
    web_cfg: dict[str, Any],
    ttl_seconds: int,
) -> list[dict[str, Any]]:
    ids = budget.take(extract_pixiv_ids(text))
    if not ids:
        return []

    cfg = get_pixiv_config()
    cookie = str(cfg.get("cookie") or "")
    proxy = str(cfg.get("proxy") or "")
    cache_dir = str(cfg.get("cache_dir") or "/tmp/hikari_bot")
    max_file_mb = max(1, int(cfg.get("max_file_mb", 25)))
    cache_ttl_seconds = ttl_seconds_from_config(
        cfg.get("cache_ttl_seconds"),
        DEFAULT_TEMP_MEDIA_TTL_SECONDS,
    )
    max_send = max(1, int(cfg.get("max_send", 6)))
    allow_r18 = bool(cfg.get("allow_r18", False))
    max_proxy_bytes = _max_proxy_bytes(web_cfg)

    items: list[dict[str, Any]] = []
    for illust_id in ids:
        source_url = f"https://www.pixiv.net/artworks/{illust_id}"
        try:
            artwork = await fetch_artwork(illust_id, cookie, proxy)
            item = _pixiv_artwork_item(
                artwork,
                source_url=source_url,
                allow_r18=allow_r18,
            )
            if artwork.is_r18 and not allow_r18:
                item["warnings"].append("Pixiv 配置未允许 R-18/R-18G，媒体未下载。")
                items.append(item)
                continue

            selected_pages = artwork.pages[:max_send]
            if len(artwork.pages) > len(selected_pages):
                item["warnings"].append(f"按 Pixiv 配置仅处理前 {len(selected_pages)} 页。")
            original_count = 0
            for page in selected_pages:
                media = None
                if download:
                    try:
                        path, is_original = await download_with_fallback(
                            page,
                            illust_id,
                            cookie,
                            proxy,
                            cache_dir,
                            max_file_mb,
                            cache_ttl_seconds=cache_ttl_seconds,
                        )
                        original_count += 1 if is_original else 0
                        media = register_file(
                            path,
                            kind="image",
                            ttl_seconds=ttl_seconds,
                            filename=f"pixiv_{illust_id}_p{page.index}{Path(path).suffix}",
                            source_url=page.original_url,
                        )
                    except Exception as e:
                        logger.exception("[MediaDetailWeb] Pixiv image download failed: %s", e)
                        media = _skipped_media("image", f"P{page.index + 1}", str(e))
                else:
                    media = register_remote(
                        page.original_url,
                        kind="image",
                        ttl_seconds=ttl_seconds,
                        filename=f"pixiv_{illust_id}_p{page.index}{get_suffix_from_url(page.original_url)}",
                        headers={
                            "Referer": source_url,
                            "User-Agent": "Mozilla/5.0",
                            **({"Cookie": cookie} if cookie else {}),
                        },
                        max_proxy_bytes=max_proxy_bytes,
                        source_url=page.original_url,
                    )
                media.update({
                    "label": f"P{page.index + 1}",
                    "width": page.width,
                    "height": page.height,
                })
                item["media"].append(media)

            item["summary"]["downloaded"] = sum(1 for media in item["media"] if media.get("status") != "skipped")
            item["details"].append({"label": "原图数量", "value": str(original_count)})
            items.append(item)
        except Exception as e:
            logger.exception("[MediaDetailWeb] Pixiv parse failed: %s", e)
            items.append(_error_item(
                platform="Pixiv",
                source="pixiv_parser",
                source_url=source_url,
                error=str(e),
            ))
    return items


def _pixiv_artwork_item(artwork: PixivArtwork, *, source_url: str, allow_r18: bool) -> dict[str, Any]:
    flags = []
    if artwork.x_restrict == 1:
        flags.append("R-18")
    elif artwork.x_restrict == 2:
        flags.append("R-18G")
    if artwork.ai_type == 2:
        flags.append("AI")
    if artwork.is_r18 and not allow_r18:
        flags.append("blocked")

    return {
        "source": "pixiv_parser",
        "platform": "Pixiv",
        "source_url": source_url,
        "title": artwork.title,
        "author": artwork.user_name,
        "description": "",
        "timestamp": "",
        "tags": artwork.tags,
        "flags": flags,
        "details": [
            {"label": "作品 ID", "value": artwork.illust_id},
            {"label": "作者 ID", "value": artwork.user_id},
            {"label": "页数", "value": str(artwork.page_count)},
            {"label": "Sanity Level", "value": str(artwork.sanity_level)},
        ],
        "summary": {
            "videos": 0,
            "images": len(artwork.pages),
            "downloaded": 0,
        },
        "media": [],
        "warnings": [],
        "error": "",
    }


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


async def _parse_cobalt_links(
    text: str,
    download: bool,
    budget: _LinkBudget,
    web_cfg: dict[str, Any],
    ttl_seconds: int,
) -> list[dict[str, Any]]:
    urls = budget.take(extract_social_urls(text))
    if not urls:
        return []

    cfg = get_cobalt_config()
    items: list[dict[str, Any]] = []
    for url in urls:
        try:
            result = await _call_cobalt_with_retries(url, cfg)
            if result.status == "error":
                items.append(_error_item(
                    platform="Cobalt",
                    source="cobalt_parser",
                    source_url=url,
                    error=f"Cobalt API 无法解析：{result.error_code or 'unknown'}",
                ))
                continue
            items.append(await _cobalt_result_item(result, cfg, web_cfg, ttl_seconds, download))
        except Exception as e:
            logger.exception("[MediaDetailWeb] Cobalt parse failed: %s", e)
            items.append(_error_item(
                platform="Cobalt",
                source="cobalt_parser",
                source_url=url,
                error=str(e),
            ))
    return items


async def _call_cobalt_with_retries(url: str, cfg: dict[str, Any]) -> CobaltResult:
    retry_count = max(0, int(cfg.get("parse_retry_count", 2)))
    retry_delay = max(0.0, float(cfg.get("parse_retry_delay_seconds", 2.0)))
    last_result: CobaltResult | None = None
    for attempt in range(retry_count + 1):
        try:
            result = await call_cobalt_api(
                url,
                str(cfg.get("cobalt_api") or "http://192.168.31.2:54257/"),
                str(cfg.get("api_key") or ""),
                int(cfg.get("api_timeout", 90)),
            )
        except Exception:
            if attempt >= retry_count:
                raise
            if retry_delay > 0:
                await asyncio.sleep(retry_delay)
            continue
        if result.status != "error":
            return result
        last_result = result
        if attempt < retry_count and retry_delay > 0:
            await asyncio.sleep(retry_delay)
    if last_result is not None:
        return last_result
    raise RuntimeError("Cobalt API 重试失败")


async def _cobalt_result_item(
    result: CobaltResult,
    cfg: dict[str, Any],
    web_cfg: dict[str, Any],
    ttl_seconds: int,
    download: bool,
) -> dict[str, Any]:
    service = result.service or "Cobalt"
    max_send = max(1, int(cfg.get("max_send", 6)))
    cache_dir = str(cfg.get("cache_dir") or "/tmp/hikari_bot")
    api_timeout = max(5, int(cfg.get("api_timeout", 90)))
    max_file_mb = max(1, int(cfg.get("max_file_mb", 200)))
    cache_ttl_seconds = ttl_seconds_from_config(
        cfg.get("cache_ttl_seconds"),
        DEFAULT_TEMP_MEDIA_TTL_SECONDS,
    )
    max_proxy_bytes = _max_proxy_bytes(web_cfg)

    item = {
        "source": "cobalt_parser",
        "platform": service.capitalize(),
        "source_url": result.source_url,
        "title": "",
        "author": "",
        "description": "",
        "timestamp": "",
        "tags": [],
        "flags": [result.status],
        "details": [
            {"label": "服务", "value": service or "unknown"},
            {"label": "媒体数量", "value": str(len(result.items))},
        ],
        "summary": {
            "videos": sum(1 for media in result.items if media.media_type == "video"),
            "images": sum(1 for media in result.items if media.media_type != "video"),
            "downloaded": 0,
        },
        "media": [],
        "warnings": [],
        "error": "",
    }

    selected = result.items[:max_send]
    if len(result.items) > len(selected):
        item["warnings"].append(f"按 Cobalt 配置仅处理前 {len(selected)} 个媒体。")

    for media_item in selected:
        kind = "video" if media_item.media_type == "video" else "image"
        label = f"{media_item.media_type} #{media_item.index + 1}"
        if download:
            try:
                path = await download_cobalt_media(
                    media_item.url,
                    "",
                    cache_dir,
                    api_timeout,
                    max_file_mb,
                    cache_ttl_seconds=cache_ttl_seconds,
                )
                media = register_file(
                    path,
                    kind=kind,
                    ttl_seconds=ttl_seconds,
                    filename=path.name,
                    source_url=media_item.url,
                )
            except Exception as e:
                logger.exception("[MediaDetailWeb] Cobalt media download failed: %s", e)
                media = _skipped_media(kind, label, str(e))
        else:
            media = register_remote(
                media_item.url,
                kind=kind,
                ttl_seconds=ttl_seconds,
                filename=f"cobalt_{media_item.index + 1}",
                max_proxy_bytes=max_proxy_bytes,
                source_url=media_item.url,
            )
        media["label"] = label
        item["media"].append(media)

    item["summary"]["downloaded"] = sum(1 for media in item["media"] if media.get("status") != "skipped")
    if result.audio_url:
        item["warnings"].append("Cobalt 返回了独立音频链接，当前页面优先展示图片/视频媒体。")
    return item


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
