"""Aggregated media parser plugin powered by astrbot_plugin_media_parser."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent

from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command
from core.error_notifier import notify_error_to_superuser, send_user_error
from core.message_pipeline import register_handler
from core.stats_tracker import increment as stats_increment
from third_party.astrbot_plugin_media_parser.core.parser.utils import extract_url_from_card_data
from third_party.astrbot_plugin_media_parser.core.storage.parse_record import ParseRecordManager

from .config import get_config
from .runtime import MediaParserRuntime, create_runtime
from .sender import send_metadata_result

logger = logging.getLogger("HikariBot.MediaParser")

get_config()

_parse_lock = asyncio.Lock()

SUPPORTED_LINK_MARKERS = (
    "bilibili.com",
    "b23.tv",
    "douyin.com",
    "iesdouyin.com",
    "tiktok.com",
    "kuaishou.com",
    "gifshow.com",
    "chenzhongtech.com",
    "weibo.com",
    "weibo.cn",
    "xiaohongshu.com",
    "xhslink.com",
    "goofish.com",
    "m.tb.cn",
    "toutiao.com",
    "xiaoheihe.cn",
    "twitter.com",
    "x.com",
)


def _event_text(event: MessageEvent) -> str:
    parts = [str(event.get_message())]
    plain = event.get_plaintext()
    if plain and plain not in parts[0]:
        parts.append(plain)
    parts.extend(_extract_card_urls(event))
    return "\n".join(part for part in parts if part)


def _extract_card_urls(event: MessageEvent) -> list[str]:
    """Extract URLs hidden in OneBot JSON/XML card segments."""
    urls: list[str] = []
    seen: set[str] = set()
    for segment in event.get_message():
        data = getattr(segment, "data", None)
        if data is None:
            continue
        for candidate in _card_url_candidates(data):
            if candidate and candidate not in seen:
                seen.add(candidate)
                urls.append(candidate)
    return urls


def _card_url_candidates(data: Any) -> list[str]:
    candidates: list[str] = []
    card_url = extract_url_from_card_data(data)
    if card_url:
        candidates.append(card_url)
    if isinstance(data, dict):
        for value in data.values():
            card_url = extract_url_from_card_data(value)
            if card_url:
                candidates.append(card_url)
    return candidates


def _runtime_from_config() -> MediaParserRuntime | None:
    cfg = get_config()
    if not cfg.get("enabled", True):
        return None
    try:
        runtime = create_runtime(cfg)
    except Exception as e:
        logger.warning("[MediaParser] runtime init skipped: %s", e)
        return None
    if not runtime.config_manager.message.has_any_output():
        return None
    return runtime


def _scope_allowed(runtime: MediaParserRuntime, event: MessageEvent) -> bool:
    is_private = not isinstance(event, GroupMessageEvent)
    group_id = None if is_private else event.group_id
    return runtime.config_manager.permission.check(
        is_private=is_private,
        sender_id=event.get_user_id(),
        group_id=group_id,
    )


def _apply_output_modes(runtime: MediaParserRuntime, metadata: dict[str, Any]) -> bool:
    text_enabled, rich_enabled = runtime.config_manager.message.output_for_metadata(metadata)
    metadata["_enable_text_metadata"] = text_enabled
    metadata["_enable_rich_media"] = rich_enabled
    if metadata.get("error"):
        return text_enabled
    if rich_enabled and (metadata.get("video_urls") or metadata.get("image_urls")):
        return True
    if text_enabled:
        return bool(
            metadata.get("title")
            or metadata.get("author")
            or metadata.get("desc")
            or metadata.get("access_message")
            or metadata.get("source_url")
        )
    return False


async def _process_text(bot: Bot, event: MessageEvent, text: str, *, force: bool = False) -> None:
    runtime = _runtime_from_config()
    if runtime is None:
        return
    if not _scope_allowed(runtime, event):
        return

    links = runtime.parser_manager.extract_all_links(text)
    if not links:
        if force:
            await bot.send(event, Message(msg("media_parser.no_link")))
        return

    if not force and not runtime.config_manager.trigger.should_parse(text):
        return

    max_links = max(1, int(runtime.config.get("max_links_per_message", 2)))
    links = links[:max_links]
    record_manager = _create_record_manager(runtime)
    if record_manager.enabled:
        links, blocked = record_manager.filter_links(
            links,
            user_key=ParseRecordManager.build_user_key("onebot", event.get_user_id()),
        )
        if blocked and force:
            await bot.send(event, Message(msg("media_parser.rate_limited", reason=blocked[0].reason)))
        if not links:
            return
    logger.info(
        "[MediaParser] parse triggered -> user=%s, links=%d",
        event.get_user_id(),
        len(links),
    )

    timeout = aiohttp.ClientTimeout(total=max(30, int(runtime.config.get("api_timeout", 120))))
    async with aiohttp.ClientSession(timeout=timeout) as session:
        metadata_list = await runtime.parser_manager.parse_text(
            text,
            session,
            links_with_parser=links,
        )
        record_manager.record_metadata_links(metadata_list)
        if not metadata_list:
            if force:
                await bot.send(event, Message(msg("media_parser.empty")))
            return

        processed: list[dict[str, Any]] = []
        for metadata in metadata_list:
            if not _apply_output_modes(runtime, metadata):
                continue
            if metadata.get("_enable_rich_media") and not metadata.get("error"):
                metadata = await runtime.download_manager.process_metadata(
                    session=session,
                    metadata=metadata,
                    proxy_addr=runtime.config_manager.proxy.address or None,
                )
            processed.append(metadata)

        if not processed:
            if force:
                await bot.send(event, Message(msg("media_parser.empty")))
            return

        total_sent = 0
        for metadata in processed:
            total_sent += await send_metadata_result(bot, event, metadata, runtime.config)
            await asyncio.sleep(0.8)

        if total_sent == 0 and not any(item.get("_enable_text_metadata") for item in processed):
            await bot.send(event, Message(msg("media_parser.no_media")))
        stats_increment(event, "media_parser_parsed", len(processed))


def _create_record_manager(runtime: MediaParserRuntime) -> ParseRecordManager:
    cfg = runtime.config_manager.parse_rate_limit
    return ParseRecordManager(
        record_file=cfg.record_file,
        same_link_max_count=cfg.same_link.max_count,
        same_link_window_seconds=cfg.same_link.window_seconds,
        same_user_max_count=cfg.same_user.max_count,
        same_user_window_seconds=cfg.same_user.window_seconds,
    )


class AutoMediaParserHandler:
    """Automatically detect and parse supported media platform links."""

    name = "MediaParser"

    async def match(self, event: MessageEvent, text: str) -> bool:
        parse_text = _event_text(event)
        lowered = parse_text.casefold()
        if not any(marker in lowered for marker in SUPPORTED_LINK_MARKERS):
            return False
        runtime = _runtime_from_config()
        if runtime is None:
            return False
        if not runtime.config_manager.trigger.should_parse(parse_text):
            return False
        return bool(runtime.parser_manager.extract_all_links(parse_text))

    async def handle(self, bot: Bot, event: MessageEvent) -> None:
        async with _parse_lock:
            await _process_text(bot, event, _event_text(event))


@command(
    "媒体解析",
    aliases=("解析媒体", "视频解析"),
    description="解析抖音/B站/小红书/小黑盒等平台链接",
    usage="媒体解析 <链接>",
)
async def media_parse_command(ctx: CommandContext) -> None:
    if not ctx.args:
        await ctx.send(Message(msg("media_parser.usage")))
        return
    async with _parse_lock:
        await _process_text(ctx.bot, ctx.event, ctx.args, force=True)


register_handler(AutoMediaParserHandler())
logger.info("Aggregated media parser registered")
