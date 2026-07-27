"""Cobalt media parsing handler (Instagram, Facebook)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from core.temp_media_cleaner import DEFAULT_TEMP_MEDIA_TTL_SECONDS, ttl_seconds_from_config
from plugins.cobalt_parser.config import get_config as get_cobalt_config
from plugins.cobalt_parser.downloader import download_media as download_cobalt_media
from plugins.cobalt_parser.parser import CobaltResult, call_cobalt_api, extract_social_urls

from .config import get_config
from .registry import register_file, register_remote
from .utils import _error_item, _LinkBudget, _max_proxy_bytes, _skipped_media

logger = logging.getLogger("HikariBot.MediaDetailWeb")


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
