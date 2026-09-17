"""OneBot sender for upstream media parser metadata."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent, Message, MessageSegment

from core.bot_identity import get_bot_name
from core.bot_messages import get_message as msg
from third_party.astrbot_plugin_media_parser.core.downloader.utils import strip_media_prefixes

logger = logging.getLogger("HikariBot.MediaParserSender")


def _as_file_uri(path: str) -> str:
    return Path(path).resolve().as_uri()


def _truncate(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)] + "..."


def _first_url(groups: list[list[str]], index: int) -> str:
    if index >= len(groups):
        return ""
    group = groups[index]
    if not group:
        return ""
    return strip_media_prefixes(group[0])


def build_metadata_text(
    metadata: dict[str, Any],
    *,
    max_desc_chars: int,
    simplified_platforms: list[str] | None = None,
    show_url: bool = True,
) -> str:
    """Build a compact text summary for one parsed link."""
    if metadata.get("error"):
        logger.info(
            "[MediaParser] suppress public parse error -> platform=%s url=%s error=%s",
            metadata.get("platform") or metadata.get("parser_name") or "unknown",
            metadata.get("source_url") or metadata.get("url") or "",
            metadata.get("error"),
        )
        return ""

    platform = metadata.get("platform") or metadata.get("parser_name") or "unknown"
    simplified = platform in (simplified_platforms or [])

    if simplified:
        # Simplified mode: only author + body text, no header/media info
        lines: list[str] = []
        if metadata.get("author"):
            lines.append(msg("media_parser.info_author", author=_truncate(str(metadata["author"]), 80)))
        desc = metadata.get("desc")
        if desc:
            if lines:
                lines.append("")
            lines.append(_truncate(str(desc), max_desc_chars))
        return "\n".join(lines)

    video_count = int(metadata.get("_original_video_count", len(metadata.get("video_urls") or [])))
    image_count = int(metadata.get("_original_image_count", len(metadata.get("image_urls") or [])))
    lines = [
        msg(
            "media_parser.info_header",
            platform=platform,
        )
    ]
    if metadata.get("title"):
        lines.append(msg("media_parser.info_title", title=_truncate(str(metadata["title"]), 120)))
    if metadata.get("author"):
        lines.append(msg("media_parser.info_author", author=_truncate(str(metadata["author"]), 80)))
    if metadata.get("timestamp"):
        lines.append(msg("media_parser.info_time", timestamp=metadata["timestamp"]))
    if video_count or image_count:
        if metadata.get("_video_only"):
            # 仅视频平台只发视频，就别在游戏信息里写「图片 N」了
            lines.append(msg("media_parser.info_media_count_video", video_count=video_count))
        else:
            lines.append(msg("media_parser.info_media_count", video_count=video_count, image_count=image_count))
    access_message = metadata.get("access_message") or metadata.get("restriction_label")
    if access_message:
        lines.append(msg("media_parser.info_access", access=_truncate(str(access_message), 120)))

    skip_reasons = [
        reason
        for reason in (metadata.get("video_skip_reasons") or []) + (metadata.get("image_skip_reasons") or [])
        if reason
    ]
    if skip_reasons:
        lines.append(msg("media_parser.info_skip", reason=_truncate("; ".join(map(str, skip_reasons)), 180)))

    source_url = metadata.get("source_url") or metadata.get("url") or ""
    if show_url and source_url:
        lines.append(msg("media_parser.info_url", url=_truncate(str(source_url), 160)))

    desc = metadata.get("desc")
    if desc:
        lines.append("")
        lines.append(_truncate(str(desc), max_desc_chars))

    hot_comments = metadata.get("hot_comments") or []
    if hot_comments:
        lines.append("")
        lines.append(msg("media_parser.hot_comments_header"))
        for item in hot_comments[:3]:
            if isinstance(item, dict):
                author = item.get("author") or item.get("user") or ""
                content = item.get("content") or item.get("text") or ""
                if content:
                    prefix = f"{author}: " if author else ""
                    lines.append(_truncate(prefix + str(content), 120))

    return "\n".join(lines)


def build_media_messages(metadata: dict[str, Any], *, max_send: int) -> list[tuple[str, Message]]:
    """Build OneBot media messages from processed metadata."""
    messages: list[tuple[str, Message]] = []
    file_paths = metadata.get("file_paths") or []
    video_urls = metadata.get("video_urls") or []
    image_urls = metadata.get("image_urls") or []
    video_modes = metadata.get("video_modes") or []
    image_modes = metadata.get("image_modes") or []
    video_count = len(video_urls)
    # 「仅视频」平台（parsers.<平台> = 仅视频）只发送视频，跳过全部图片。
    video_only = bool(metadata.get("_video_only"))

    for index, mode in enumerate(video_modes):
        if len(messages) >= max_send:
            break
        uri = ""
        if mode == "local" and index < len(file_paths) and file_paths[index]:
            uri = _as_file_uri(str(file_paths[index]))
        elif mode == "direct":
            uri = _first_url(video_urls, index)
        if uri:
            messages.append(("video", Message(MessageSegment.video(uri))))

    if video_only:
        return messages

    for index, mode in enumerate(image_modes):
        if len(messages) >= max_send:
            break
        position = video_count + index
        uri = ""
        if mode == "local" and position < len(file_paths) and file_paths[position]:
            uri = _as_file_uri(str(file_paths[position]))
        elif mode == "direct":
            uri = _first_url(image_urls, index)
        if uri:
            messages.append(("image", Message(MessageSegment.image(uri))))

    return messages


async def send_metadata_result(
    bot: Bot,
    event: Event,
    metadata: dict[str, Any],
    config: dict[str, Any],
) -> int:
    """Send one processed metadata item. Returns the number of media nodes sent."""
    message_cfg = config.get("message") or {}
    text_cfg = message_cfg.get("text_metadata") or {}
    send_strategy = config.get("send_strategy") or {}
    max_send = max(1, int(config.get("max_send", 8)))
    max_desc_chars = max(0, int(text_cfg.get("max_desc_chars", 600)))
    show_url = bool(text_cfg.get("show_url", True))
    simplified_platforms = message_cfg.get("simplified_output") or []

    text_enabled = bool(metadata.get("_enable_text_metadata", True))
    rich_enabled = bool(metadata.get("_enable_rich_media", True))

    text = (
        build_metadata_text(
            metadata,
            max_desc_chars=max_desc_chars,
            simplified_platforms=simplified_platforms,
            show_url=show_url,
        )
        if text_enabled else
        ""
    )
    media_messages = build_media_messages(metadata, max_send=max_send) if rich_enabled else []

    prefer_forward = bool(send_strategy.get("prefer_forward_message", True))
    fallback_separate = bool(send_strategy.get("fallback_to_separate_media", True))
    include_text_in_forward = bool(send_strategy.get("include_text_in_forward", True))
    forward_timeout_seconds = max(1.0, float(send_strategy.get("forward_timeout_seconds", 90)))

    if prefer_forward and len(media_messages) > 1:
        # 多个媒体一律合并转发：视频也一起进聊天记录，游戏信息等文本作为首条节点发出。
        # 一条转发装不下就按 max_send 拆成多条聊天记录，但绝不改成逐条发送。
        logger.info(
            "[MediaParser] sending media as forward chunks -> media=%d chunk_size=%d timeout=%.1fs",
            len(media_messages),
            max_send,
            forward_timeout_seconds,
        )
        sent_count, text_sent, failure = await _send_forward_chunks(
            bot,
            event,
            text=text,
            media_messages=media_messages,
            include_text=include_text_in_forward,
            chunk_size=max_send,
            timeout_seconds=forward_timeout_seconds,
        )
        if sent_count == len(media_messages):
            if text and not text_sent:
                await bot.send(event, Message(text))
            return sent_count
        if not fallback_separate:
            # 合并转发没走完（被 NapCat 拒绝或超时）就什么都不发：NapCat 的转发上传是异步的，
            # 超时后往往仍在后台上传并最终送达，这时逐条补发会让同一批媒体发两遍。
            logger.warning(
                "[MediaParser] forward %s, fallback disabled -> nothing sent -> sent_media=%d total_media=%d",
                failure,
                sent_count,
                len(media_messages),
            )
            return sent_count
        logger.info(
            "[MediaParser] falling back to separate media sends -> remaining=%d total=%d",
            len(media_messages) - sent_count,
            len(media_messages),
        )
        return await _send_separate(
            bot,
            event,
            text=text,
            media_messages=media_messages,
            start_index=sent_count,
            send_text=bool(text and not text_sent),
        )

    return await _send_separate(
        bot,
        event,
        text=text,
        media_messages=media_messages,
        start_index=0,
        send_text=bool(text),
    )


async def _send_forward_chunks(
    bot: Bot,
    event: Event,
    *,
    text: str,
    media_messages: list[tuple[str, Message]],
    include_text: bool,
    chunk_size: int,
    timeout_seconds: float,
) -> tuple[int, bool, str]:
    """发送合并转发分片，返回 (已发送媒体数, 文本是否发出, 失败原因)。"""
    sent_count = 0
    text_sent = False
    for chunk_index, chunk in enumerate(_chunk_media_messages(media_messages, chunk_size)):
        nodes: list[MessageSegment] = []
        include_text_node = chunk_index == 0 and bool(text) and include_text
        if include_text_node:
            nodes.append(_node(bot, Message(text)))
        for _, media in chunk:
            nodes.append(_node(bot, media))
        status = await _try_send_forward(bot, event, nodes, timeout_seconds=timeout_seconds)
        if status is None:
            return sent_count, text_sent, "timed out"
        if not status:
            logger.warning(
                "[MediaParser] forward chunk failed -> chunk=%d sent_media=%d total_media=%d",
                chunk_index + 1,
                sent_count,
                len(media_messages),
            )
            return sent_count, text_sent, "rejected"
        sent_count += len(chunk)
        text_sent = text_sent or include_text_node
        logger.info(
            "[MediaParser] forward chunk sent -> chunk=%d sent_media=%d total_media=%d",
            chunk_index + 1,
            sent_count,
            len(media_messages),
        )
    return sent_count, text_sent, ""


def _chunk_media_messages(
    media_messages: list[tuple[str, Message]],
    chunk_size: int,
) -> list[list[tuple[str, Message]]]:
    chunk_size = max(1, chunk_size)
    return [
        media_messages[index:index + chunk_size]
        for index in range(0, len(media_messages), chunk_size)
    ]


async def _send_separate(
    bot: Bot,
    event: Event,
    *,
    text: str,
    media_messages: list[tuple[str, Message]],
    start_index: int,
    send_text: bool,
) -> int:
    if send_text and text:
        await bot.send(event, Message(text))
    for index, (_, media) in enumerate(media_messages[start_index:], start=start_index + 1):
        await bot.send(event, media)
        logger.info(
            "[MediaParser] separate media sent -> index=%d total=%d",
            index,
            len(media_messages),
        )
        await asyncio.sleep(0.4)
    return len(media_messages)


def _node(bot: Bot, content: Message) -> MessageSegment:
    return MessageSegment.node_custom(
        user_id=int(bot.self_id),
        nickname=get_bot_name(),
        content=content,
    )


async def _try_send_forward(
    bot: Bot,
    event: Event,
    nodes: list[MessageSegment],
    *,
    timeout_seconds: float,
) -> bool | None:
    """发送一条合并转发。

    Returns:
        True 已送达；False 明确失败（可以安全回退逐条发送）；None 超时——NapCat 可能
        仍在后台上传并最终送达，此时不能补发。
    """
    try:
        async def _send() -> None:
            if isinstance(event, GroupMessageEvent):
                await bot.send_group_forward_msg(group_id=event.group_id, messages=nodes)
            else:
                await bot.send_private_forward_msg(user_id=int(event.get_user_id()), messages=nodes)

        await asyncio.wait_for(_send(), timeout=timeout_seconds)
        return True
    except asyncio.TimeoutError:
        logger.warning("[MediaParser] forward message timed out after %.1fs", timeout_seconds)
        return None
    except Exception as e:
        logger.warning("[MediaParser] forward message failed: %s", e)
        return False
