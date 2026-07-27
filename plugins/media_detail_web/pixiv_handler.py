"""Pixiv media parsing handler."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from core.temp_media_cleaner import DEFAULT_TEMP_MEDIA_TTL_SECONDS, ttl_seconds_from_config
from plugins.pixiv_parser.config import get_config as get_pixiv_config
from plugins.pixiv_parser.downloader import download_with_fallback, get_suffix_from_url
from plugins.pixiv_parser.parser import PixivArtwork, extract_pixiv_ids, fetch_artwork

from .config import get_config
from .registry import register_file, register_remote
from .utils import _error_item, _LinkBudget, _max_proxy_bytes, _skipped_media

logger = logging.getLogger("HikariBot.MediaDetailWeb")


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
