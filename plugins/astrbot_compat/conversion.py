"""OneBot message conversion utilities.

Handles conversion from AstrBot message components to OneBot
``MessageSegment`` objects, including automatic file-offloading of
large base64 images to avoid exceeding NapCat's 1 MB WebSocket
frame limit.
"""

from __future__ import annotations

import base64
import logging
import time
import uuid
from pathlib import Path

from nonebot.adapters.onebot.v11 import MessageSegment

from astrbot.api.message_components import (
    BaseMessageComponent,
    Image,
    Json as JsonComp,
    Node as NodeComp,
    Plain,
    Record,
    Reply as ReplyComp,
    Share as ShareComp,
    Video,
)
from astrbot.core.message.message_event_result import MessageChain

logger = logging.getLogger("AstrBotCompat.Conversion")

# ---------------------------------------------------------------------------
# Oversized-image offloading
# ---------------------------------------------------------------------------

# Images larger than this threshold (raw decoded bytes) will be saved to
# a temp file and sent via ``file://`` URI instead of inline base64.
_MAX_INLINE_IMAGE_BYTES = 512_000  # 500 KB — well under the 1 MB WS limit

# Shared temp directory (accessible by both bot and NapCat in Docker).
# Docker mounts ./runtime/shared → /app/sharedFolder on both containers,
# so "sharedFolder/" resolves to the shared volume on each side.
_TEMP_DIR = Path("sharedFolder/astrbot_temp")
_TEMP_DIR_EXPIRY_SECONDS = 900  # 15 min — files cleaned on best-effort basis


def _save_oversized_image(data: bytes, ext: str = ".png") -> str | None:
    """Save a large image to the shared temp directory.

    Returns a ``file://`` URI the OneBot implementation can read, or
    ``None`` if the save fails (caller should fall back to base64).
    """
    try:
        _TEMP_DIR.mkdir(parents=True, exist_ok=True)
        name = f"astrbot_{uuid.uuid4().hex[:12]}_{int(time.time())}{ext}"
        path = _TEMP_DIR / name
        path.write_bytes(data)
        logger.debug("Saved oversized image to %s (%d bytes)", path, len(data))
        # Register for TTL cleanup
        try:
            from core.temp_media_cleaner import register_temp_media_path

            register_temp_media_path(path, _TEMP_DIR_EXPIRY_SECONDS)
        except Exception:
            pass
        # Construct a ``file://`` URI.
        # In Docker: both bot and NapCat see /app/sharedFolder/
        # On bare metal: NapCat sees the same absolute path.
        abs_path = path.resolve()
        uri = abs_path.as_uri()
        logger.debug("Image URI: %s", uri)
        return uri
    except Exception as e:
        logger.warning("Failed to save oversized image: %s", e)
        return None


# ---------------------------------------------------------------------------
# Cleanup helper (called on bot startup)
# ---------------------------------------------------------------------------


def clean_stale_temp_files() -> None:
    """Remove temp image files older than ``_TEMP_DIR_EXPIRY_SECONDS``."""
    if not _TEMP_DIR.exists():
        return
    now = time.time()
    removed = 0
    for f in _TEMP_DIR.iterdir():
        if f.is_file() and f.name.startswith("astrbot_"):
            try:
                age = now - f.stat().st_mtime
                if age > _TEMP_DIR_EXPIRY_SECONDS:
                    f.unlink()
                    removed += 1
            except OSError:
                pass
    if removed:
        logger.debug("Cleaned %d stale temp files from %s", removed, _TEMP_DIR)


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------


def convert_chain_to_onebot(chain: MessageChain) -> str | list[MessageSegment]:
    """Convert a ``MessageChain`` to a OneBot-compatible message object."""
    segments: list[MessageSegment] = []

    for comp in chain.chain:
        seg = _component_to_segment(comp)
        if seg is not None:
            segments.append(seg)

    if not segments:
        return ""

    if len(segments) == 1 and segments[0].type == "text":
        return segments[0].data.get("text", "")

    return segments


def _component_to_segment(comp: BaseMessageComponent) -> MessageSegment | None:
    if isinstance(comp, Plain):
        return MessageSegment.text(comp.text)

    if isinstance(comp, Image):
        return _image_to_segment(comp)

    if isinstance(comp, Record):
        url = comp.url or comp.file or comp.path
        if url:
            return MessageSegment.record(url)
        return None

    if isinstance(comp, Video):
        url = comp.url or comp.file
        if url:
            return MessageSegment.video(url)
        return None

    if isinstance(comp, ReplyComp):
        text = f"[回复 {comp.id}]"
        if comp.message_str:
            text += f" {comp.message_str}"
        elif comp.sender_nickname:
            text += f" ({comp.sender_nickname})"
        return MessageSegment.text(text)

    if isinstance(comp, ShareComp):
        return MessageSegment.text(f"🔗 {comp.title}: {comp.url}")

    if isinstance(comp, JsonComp) and comp.data:
        import json

        try:
            return MessageSegment.json(json.dumps(comp.data, ensure_ascii=False))
        except (TypeError, ValueError):
            return MessageSegment.text(str(comp.data))

    if isinstance(comp, NodeComp):
        texts = []
        for child in comp.content or []:
            seg = _component_to_segment(child)
            if seg and seg.type == "text":
                texts.append(seg.data.get("text", ""))
        return MessageSegment.text("[转发消息] " + " | ".join(texts)) if texts else None

    if hasattr(comp, "text") and comp.text:
        return MessageSegment.text(str(comp.text))

    return None


def _image_to_segment(img: Image) -> MessageSegment | None:
    """Convert an Image component, offloading oversized base64 to file."""
    # Priority: url > file (as path/URI) > path
    if img.url:
        return MessageSegment.image(img.url)

    # Base64 image — check size and offload if too large
    if img._type == "base64" and img.file:
        try:
            raw = base64.b64decode(img.file)
            if len(raw) > _MAX_INLINE_IMAGE_BYTES:
                uri = _save_oversized_image(raw)
                if uri:
                    return MessageSegment.image(uri)
            # Fall through: send as base64:// inline
            return MessageSegment.image(f"base64://{img.file}")
        except Exception as e:
            logger.warning("Failed to process base64 image: %s", e)
            return MessageSegment.image(f"base64://{img.file}")

    if img.file:
        return MessageSegment.image(img.file)

    if img.path:
        return MessageSegment.image(img.path)

    return None
