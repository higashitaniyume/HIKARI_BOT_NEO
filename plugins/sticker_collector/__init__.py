from __future__ import annotations

import asyncio
import logging
import mimetypes
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, PrivateMessageEvent

from plugins import sticker_inbox
from plugins.media_transcoder import STICKER_INPUT_EXTS, TranscodeError, ensure_sticker_gif

from .config import get_config

logger = logging.getLogger("HikariBot.StickerCollector")

collector_matcher = on_message(priority=80, block=False)
_collect_sem = asyncio.Semaphore(1)


def _as_str_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value}


def _event_allowed(event: MessageEvent, cfg: dict[str, Any], bot: Bot) -> bool:
    if not cfg.get("enabled", True):
        return False

    user_id = str(event.get_user_id())
    if user_id == str(bot.self_id):
        return False
    if user_id in _as_str_set(cfg.get("ignored_users")):
        return False

    if isinstance(event, GroupMessageEvent):
        if not cfg.get("collect_group", True):
            return False
        allowed_groups = _as_str_set(cfg.get("allowed_groups"))
        return not allowed_groups or str(event.group_id) in allowed_groups

    if isinstance(event, PrivateMessageEvent):
        return bool(cfg.get("collect_private", True))

    return False


def _image_segments(event: MessageEvent) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for segment in event.get_message():
        if getattr(segment, "type", "") != "image":
            continue
        data = dict(getattr(segment, "data", {}) or {})
        if data.get("url"):
            segments.append(data)
    return segments


def _guess_suffix(image_data: dict[str, Any], url: str, content_type: str = "") -> str:
    candidates = [
        str(image_data.get("file") or ""),
        urlparse(url).path,
    ]
    for candidate in candidates:
        suffix = Path(candidate).suffix.lower()
        if suffix in STICKER_INPUT_EXTS:
            return suffix

    suffix = mimetypes.guess_extension(content_type.split(";", 1)[0].strip()) if content_type else ""
    if suffix == ".jpe":
        suffix = ".jpg"
    if suffix in STICKER_INPUT_EXTS:
        return suffix
    return ".jpg"


async def _download_image(url: str, dest: Path, timeout_seconds: float) -> str:
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        dest.write_bytes(response.content)
        return response.headers.get("content-type", "")


def _event_metadata(event: MessageEvent, image_data: dict[str, Any]) -> dict[str, Any]:
    group_id = str(getattr(event, "group_id", "") or "")
    return {
        "source": "qq_message",
        "sender_id": str(event.get_user_id()),
        "group_id": group_id,
        "message_id": str(getattr(event, "message_id", "") or ""),
        "created_at": int(time.time()),
        "original_name": str(image_data.get("file") or "qq_image"),
    }


async def _collect_one(bot: Bot, event: MessageEvent, image_data: dict[str, Any]) -> None:
    cfg = get_config()
    async with _collect_sem:
        temp_root = Path(str(cfg.get("temp_root", "/tmp/hikari_bot/sticker_collector")))
        temp_root.mkdir(parents=True, exist_ok=True)
        timeout_seconds = float(cfg.get("download_timeout_seconds", 30))
        max_pending = int(cfg.get("max_pending", 1000))
        url = str(image_data.get("url") or "")
        if not url:
            return

        raw_path: Path | None = None
        gif_path: Path | None = None
        try:
            raw_path = temp_root / f"raw_{uuid.uuid4().hex}.bin"
            content_type = await _download_image(url, raw_path, timeout_seconds)
            suffix = _guess_suffix(image_data, url, content_type)
            typed_path = raw_path.with_suffix(suffix)
            raw_path.replace(typed_path)
            raw_path = typed_path

            gif_path = temp_root / f"gif_{uuid.uuid4().hex}.gif"
            await ensure_sticker_gif(raw_path, gif_path)
            added, reason = sticker_inbox.add_gif(
                gif_path,
                metadata=_event_metadata(event, image_data),
                max_pending=max_pending,
            )
            if added:
                logger.info("[StickerCollector] 已静默收集贴纸 → %s", reason)
            else:
                logger.debug("[StickerCollector] 跳过贴纸收集: %s", reason)
        except TranscodeError as e:
            logger.debug("[StickerCollector] 图片转 GIF 失败，已跳过: %s", e)
        except Exception as e:
            logger.debug("[StickerCollector] 静默收集图片失败，已跳过: %s", e)
        finally:
            if raw_path is not None:
                raw_path.unlink(missing_ok=True)
            if gif_path is not None:
                gif_path.unlink(missing_ok=True)


async def _collect_message(bot: Bot, event: MessageEvent, images: list[dict[str, Any]]) -> None:
    for image_data in images:
        await _collect_one(bot, event, image_data)


@collector_matcher.handle()
async def handle_collect_stickers(bot: Bot, event: MessageEvent) -> None:
    cfg = get_config()
    if not _event_allowed(event, cfg, bot):
        return

    images = _image_segments(event)
    if not images:
        return

    # 静默后台收集，不阻塞聊天消息处理。
    asyncio.create_task(_collect_message(bot, event, images))
