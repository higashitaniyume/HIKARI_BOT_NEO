"""Aggregated media parser plugin powered by astrbot_plugin_media_parser."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent

from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command
from core.message_pipeline import register_handler
from core.stats_tracker import increment as stats_increment
from third_party.astrbot_plugin_media_parser.core.parser.utils import extract_url_from_card_data
from third_party.astrbot_plugin_media_parser.core.storage.parse_record import ParseRecordManager

from .bilibili_cookie_assist import bilibili_cookie_assist
from .cache_cleanup import media_cache_ttl_seconds, register_metadata_temp_media
from .config import get_config
from .runtime import MediaParserRuntime, create_runtime
from .sender import send_metadata_result

logger = logging.getLogger("HikariBot.MediaParser")

get_config()

_parse_queue: asyncio.Queue["MediaParseQueueItem"] | None = None
_parse_worker_tasks: set[asyncio.Task[None]] = set()
_send_queues: dict[str, asyncio.Queue["MediaSendQueueItem"]] = {}
_send_worker_tasks: dict[str, asyncio.Task[None]] = {}

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


@dataclass
class MediaParseQueueItem:
    bot: Bot
    event: MessageEvent
    text: str
    links_with_parser: list[tuple[str, Any]]
    force: bool = False


@dataclass
class MediaSendQueueItem:
    bot: Bot
    event: MessageEvent
    processed: list[dict[str, Any]]
    config: dict[str, Any]
    force: bool = False


@dataclass
class MediaPrepareAttempt:
    processed: list[dict[str, Any]]
    metadata_list: list[dict[str, Any]]
    config: dict[str, Any]


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


def _queue_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    raw = cfg.get("parse_queue") if isinstance(cfg.get("parse_queue"), dict) else {}
    return {
        "enabled": bool(raw.get("enabled", True)),
        "max_size": max(1, int(raw.get("max_size", 100))),
        "max_concurrent": max(1, int(raw.get("max_concurrent", 2))),
        "delay_seconds": max(0.0, float(raw.get("delay_seconds", 0.8))),
    }


def _retry_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "count": max(0, int(cfg.get("parse_retry_count", 2))),
        "delay_seconds": max(0.0, float(cfg.get("parse_retry_delay_seconds", 2.0))),
    }


def _ensure_parse_workers(cfg: dict[str, Any]) -> asyncio.Queue[MediaParseQueueItem]:
    global _parse_queue
    settings = _queue_settings(cfg)
    if _parse_queue is None:
        _parse_queue = asyncio.Queue(maxsize=settings["max_size"])
    alive_workers = {task for task in _parse_worker_tasks if not task.done()}
    _parse_worker_tasks.clear()
    _parse_worker_tasks.update(alive_workers)
    while len(_parse_worker_tasks) < settings["max_concurrent"]:
        worker_no = len(_parse_worker_tasks) + 1
        task = asyncio.create_task(
            _parse_worker(),
            name=f"HikariMediaParserParseQueue-{worker_no}",
        )
        _parse_worker_tasks.add(task)
        task.add_done_callback(_parse_worker_tasks.discard)
    return _parse_queue


async def _parse_worker() -> None:
    from core.error_notifier import notify_error_to_superuser, send_user_error

    logger.info("[MediaParser] parse queue worker started")
    while True:
        assert _parse_queue is not None
        item = await _parse_queue.get()
        try:
            cfg = get_config()
            await _process_parse_item(item)
            delay = _queue_settings(cfg)["delay_seconds"]
            if delay > 0:
                await asyncio.sleep(delay)
        except Exception as e:
            logger.exception("[MediaParser] queued parse failed: %s", e)
            try:
                await send_user_error(item.bot, item.event)
                await notify_error_to_superuser(item.bot, item.event, e, "MediaParser")
            except Exception as notify_err:
                logger.exception("发送错误通知失败: %s", notify_err)
        finally:
            _parse_queue.task_done()


async def _enqueue_text(bot: Bot, event: MessageEvent, text: str, *, force: bool = False) -> None:
    runtime = _runtime_from_config()
    if runtime is None:
        return
    if not _scope_allowed(runtime, event):
        return
    if not force and not runtime.config_manager.trigger.should_parse(text):
        return

    links = runtime.parser_manager.extract_all_links(text)
    if not links:
        if force:
            await bot.send(event, Message(msg("media_parser.no_link")))
        return

    max_links = max(1, int(runtime.config.get("max_links_per_message", 20)))
    links = links[:max_links]
    settings = _queue_settings(runtime.config)
    if not settings["enabled"]:
        for link_item in links:
            await _process_parse_item(MediaParseQueueItem(
                bot=bot,
                event=event,
                text=link_item[0],
                links_with_parser=[link_item],
                force=force,
            ))
        return

    queue = _ensure_parse_workers(runtime.config)
    if queue.full():
        logger.warning(
            "[MediaParser] parse queue full -> user=%s, size=%d",
            event.get_user_id(),
            queue.qsize(),
        )
        if force:
            await bot.send(event, Message(msg("media_parser.rate_limited", reason="解析队列已满，请稍后再试")))
        return

    queued = 0
    dropped = 0
    for link_item in links:
        if queue.full():
            dropped += 1
            continue
        queue.put_nowait(MediaParseQueueItem(
            bot=bot,
            event=event,
            text=link_item[0],
            links_with_parser=[link_item],
            force=force,
        ))
        queued += 1
    if dropped and force:
        await bot.send(event, Message(msg("media_parser.rate_limited", reason=f"解析队列已满，{dropped} 个链接未入队")))
    logger.info(
        "[MediaParser] queued parse jobs -> user=%s, count=%d, dropped=%d, queue_size=%d",
        event.get_user_id(),
        queued,
        dropped,
        queue.qsize(),
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


def _conversation_key(bot: Bot, event: MessageEvent) -> str:
    bot_id = getattr(bot, "self_id", "bot")
    if isinstance(event, GroupMessageEvent):
        return f"{bot_id}:group:{event.group_id}"
    return f"{bot_id}:private:{event.get_user_id()}"


def _ensure_send_worker(key: str) -> asyncio.Queue[MediaSendQueueItem]:
    queue = _send_queues.get(key)
    if queue is None:
        queue = asyncio.Queue()
        _send_queues[key] = queue
    task = _send_worker_tasks.get(key)
    if task is None or task.done():
        task = asyncio.create_task(_send_worker(key), name=f"HikariMediaParserSendQueue-{key}")
        _send_worker_tasks[key] = task
        task.add_done_callback(lambda done_task: _clear_send_worker(key, done_task))
    return queue


def _clear_send_worker(key: str, task: asyncio.Task[None]) -> None:
    if _send_worker_tasks.get(key) is task:
        _send_worker_tasks.pop(key, None)


async def _enqueue_send(item: MediaSendQueueItem) -> None:
    key = _conversation_key(item.bot, item.event)
    queue = _ensure_send_worker(key)
    queue.put_nowait(item)
    logger.info(
        "[MediaParser] queued send job -> target=%s, items=%d, queue_size=%d",
        key,
        len(item.processed),
        queue.qsize(),
    )


async def _send_worker(key: str) -> None:
    from core.error_notifier import notify_error_to_superuser, send_user_error

    logger.info("[MediaParser] send queue worker started -> target=%s", key)
    while True:
        queue = _send_queues.get(key)
        if queue is None:
            return
        item = await queue.get()
        try:
            await _send_processed_item(item)
        except Exception as e:
            logger.exception("[MediaParser] queued send failed: %s", e)
            try:
                if item.force:
                    await send_user_error(item.bot, item.event)
                await notify_error_to_superuser(item.bot, item.event, e, "MediaParser")
            except Exception as notify_err:
                logger.exception("发送错误通知失败: %s", notify_err)
        finally:
            queue.task_done()


async def _send_processed_item(item: MediaSendQueueItem) -> None:
    total_sent = 0
    for metadata in item.processed:
        total_sent += await send_metadata_result(item.bot, item.event, metadata, item.config)
        await asyncio.sleep(0.8)

    if total_sent == 0 and not any(metadata.get("_enable_text_metadata") for metadata in item.processed):
        await item.bot.send(item.event, Message(msg("media_parser.no_media")))
    stats_increment(item.event, "media_parser_parsed", len(item.processed))


async def _process_parse_item(item: MediaParseQueueItem) -> None:
    result = await _prepare_text(
        item.bot,
        item.event,
        item.text,
        force=item.force,
        links_with_parser=item.links_with_parser,
    )
    if result is None:
        return
    processed, config = result
    await _enqueue_send(MediaSendQueueItem(
        bot=item.bot,
        event=item.event,
        processed=processed,
        config=config,
        force=item.force,
    ))


async def _prepare_text(
    bot: Bot,
    event: MessageEvent,
    text: str,
    *,
    force: bool = False,
    links_with_parser: list[tuple[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    runtime = _runtime_from_config()
    if runtime is None:
        return None
    if not _scope_allowed(runtime, event):
        return None

    links = list(links_with_parser) if links_with_parser is not None else runtime.parser_manager.extract_all_links(text)
    if not links:
        if force:
            await bot.send(event, Message(msg("media_parser.no_link")))
        return None

    if not force and not runtime.config_manager.trigger.should_parse(text):
        return None

    if links_with_parser is None:
        max_links = max(1, int(runtime.config.get("max_links_per_message", 20)))
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
            return None
    logger.info(
        "[MediaParser] parse triggered -> user=%s, links=%d",
        event.get_user_id(),
        len(links),
    )

    result = await _prepare_links_with_retries(bot, event, text, links, initial_config=runtime.config)
    if result is None:
        return None
    if record_manager.enabled:
        record_manager.record_metadata_links(result.metadata_list)
    if not result.processed:
        if force:
            await bot.send(event, Message(msg("media_parser.empty")))
        return None
    return result.processed, result.config


async def _prepare_links_with_retries(
    bot: Bot,
    event: MessageEvent,
    text: str,
    links: list[tuple[str, Any]],
    *,
    initial_config: dict[str, Any],
) -> MediaPrepareAttempt | None:
    retry = _retry_settings(initial_config)
    attempts = retry["count"] + 1
    delay_seconds = retry["delay_seconds"]
    last_result: MediaPrepareAttempt | None = None

    for attempt in range(1, attempts + 1):
        try:
            result = await _prepare_links_once(bot, event, text, links)
        except Exception as e:
            if attempt >= attempts:
                raise
            logger.warning(
                "[MediaParser] parse/download attempt failed, retrying in %.1fs -> attempt=%d/%d error=%s",
                delay_seconds,
                attempt,
                retry["count"],
                e,
                exc_info=True,
            )
        else:
            if result is None:
                return None
            if not _should_retry_prepare_result(result):
                if attempt > 1:
                    logger.info("[MediaParser] parse/download retry succeeded -> attempt=%d/%d", attempt, attempts)
                return result
            last_result = result
            if attempt >= attempts:
                return result
            logger.warning(
                "[MediaParser] parse/download produced retryable result, retrying in %.1fs -> attempt=%d/%d reason=%s",
                delay_seconds,
                attempt,
                retry["count"],
                _prepare_retry_reason(result),
            )

        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)

    return last_result


async def _prepare_links_once(
    bot: Bot,
    event: MessageEvent,
    text: str,
    links: list[tuple[str, Any]],
) -> MediaPrepareAttempt | None:
    runtime = _runtime_from_config()
    if runtime is None:
        return None
    if not _scope_allowed(runtime, event):
        return None
    links = _links_for_runtime(runtime, links)
    if not links:
        return MediaPrepareAttempt(processed=[], metadata_list=[], config=runtime.config)

    timeout = aiohttp.ClientTimeout(total=max(30, int(runtime.config.get("api_timeout", 120))))
    async with aiohttp.ClientSession(timeout=timeout) as session:
        metadata_list = await runtime.parser_manager.parse_text(
            text,
            session,
            links_with_parser=links,
        )
        _trigger_bilibili_cookie_assist_if_needed(bot, runtime)
        if not metadata_list:
            return MediaPrepareAttempt(processed=[], metadata_list=[], config=runtime.config)
        raw_metadata_list = list(metadata_list)
        metadata_list = _suppress_redundant_error_metadata(metadata_list)

        processed: list[dict[str, Any]] = []
        max_send = max(1, int(runtime.config.get("max_send", 8)))
        cache_ttl_seconds = media_cache_ttl_seconds(runtime.config)
        for metadata in metadata_list:
            if not _apply_output_modes(runtime, metadata):
                continue
            if metadata.get("_enable_rich_media") and not metadata.get("error"):
                metadata = _limit_metadata_for_send(metadata, max_send=max_send)
                metadata = await runtime.download_manager.process_metadata(
                    session=session,
                    metadata=metadata,
                    proxy_addr=runtime.config_manager.proxy.address or None,
                )
                register_metadata_temp_media(metadata, ttl_seconds=cache_ttl_seconds)
            processed.append(metadata)

        return MediaPrepareAttempt(processed=processed, metadata_list=raw_metadata_list, config=runtime.config)


def _links_for_runtime(runtime: MediaParserRuntime, links: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
    refreshed: list[tuple[str, Any]] = []
    for url, parser in links:
        runtime_parser = runtime.parser_manager.find_parser(url)
        if runtime_parser is None:
            parser_name = getattr(parser, "name", "unknown")
            logger.debug("[MediaParser] parser disabled while retrying -> parser=%s url=%s", parser_name, url)
            continue
        refreshed.append((url, runtime_parser))
    return refreshed


def _should_retry_prepare_result(result: MediaPrepareAttempt) -> bool:
    if not result.processed:
        return not result.metadata_list or any(metadata.get("error") for metadata in result.metadata_list)

    for metadata in result.processed:
        if _metadata_has_sendable_media(metadata):
            return False
        if metadata.get("error"):
            return True
        if metadata.get("_enable_rich_media") and _metadata_has_retryable_download_failure(metadata):
            return True
    return False


def _metadata_has_sendable_media(metadata: dict[str, Any]) -> bool:
    if metadata.get("has_valid_media"):
        return True
    modes = list(metadata.get("video_modes") or []) + list(metadata.get("image_modes") or [])
    return any(mode in ("local", "direct") for mode in modes)


def _metadata_has_retryable_download_failure(metadata: dict[str, Any]) -> bool:
    media_count = int(metadata.get("video_count", len(metadata.get("video_urls") or [])))
    media_count += int(metadata.get("image_count", len(metadata.get("image_urls") or [])))
    if media_count <= 0:
        return False

    status_codes = list(metadata.get("video_status_codes") or []) + list(metadata.get("image_status_codes") or [])
    for code in status_codes:
        if _is_retryable_status_code(code):
            return True

    terminal_tokens = ("超过限制", "缓存目录不可用", "403", "Forbidden", "权限")
    retry_tokens = ("缓存下载失败", "下载媒体失败", "HTTP 404", "timeout", "timed out", "超时")
    for reason in _metadata_skip_reasons(metadata):
        if any(token in reason for token in terminal_tokens):
            continue
        if any(token in reason for token in retry_tokens):
            return True
    return False


def _is_retryable_status_code(code: Any) -> bool:
    try:
        status_code = int(code)
    except (TypeError, ValueError):
        return False
    return status_code in {404, 408, 409, 425, 429} or status_code >= 500


def _metadata_skip_reasons(metadata: dict[str, Any]) -> list[str]:
    reasons = []
    for value in (metadata.get("video_skip_reasons") or []) + (metadata.get("image_skip_reasons") or []):
        if value:
            reasons.append(str(value))
    return reasons


def _prepare_retry_reason(result: MediaPrepareAttempt) -> str:
    if not result.metadata_list:
        return "empty metadata"
    errors = [str(metadata.get("error")) for metadata in result.metadata_list if metadata.get("error")]
    if errors:
        return errors[0][:160]
    for metadata in result.processed:
        reasons = _metadata_skip_reasons(metadata)
        if reasons:
            return reasons[0][:160]
    return "no sendable media"


def _limit_metadata_for_send(metadata: dict[str, Any], *, max_send: int) -> dict[str, Any]:
    video_urls = list(metadata.get("video_urls") or [])
    image_urls = list(metadata.get("image_urls") or [])
    total_count = len(video_urls) + len(image_urls)
    if total_count <= max_send:
        return metadata

    keep_video_count = min(len(video_urls), max_send)
    keep_image_count = max(0, max_send - keep_video_count)
    limited = dict(metadata)
    limited["_original_video_count"] = len(video_urls)
    limited["_original_image_count"] = len(image_urls)
    limited["video_urls"] = video_urls[:keep_video_count]
    limited["image_urls"] = image_urls[:keep_image_count]
    _slice_metadata_list(limited, "video_cover_urls", keep_video_count)
    _slice_metadata_list(limited, "video_cover_url_lists", keep_video_count)
    _slice_metadata_list(limited, "video_force_downloads", keep_video_count)
    logger.info(
        "[MediaParser] media list limited before download -> platform=%s original=%d keep=%d",
        metadata.get("platform") or metadata.get("parser_name") or "unknown",
        total_count,
        keep_video_count + keep_image_count,
    )
    return limited


def _slice_metadata_list(metadata: dict[str, Any], key: str, limit: int) -> None:
    value = metadata.get(key)
    if isinstance(value, list):
        metadata[key] = value[:limit]


def _suppress_redundant_error_metadata(metadata_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop failed candidate links when the same message produced a success."""
    successes = [item for item in metadata_list if not item.get("error")]
    failures = [item for item in metadata_list if item.get("error")]
    if not successes or not failures:
        return metadata_list

    for item in failures:
        logger.info(
            "[MediaParser] suppress failed candidate because another candidate succeeded -> "
            "platform=%s url=%s error=%s",
            item.get("platform") or item.get("parser_name") or "unknown",
            item.get("source_url") or item.get("url") or "",
            item.get("error"),
        )
    return successes


def _create_record_manager(runtime: MediaParserRuntime) -> ParseRecordManager:
    cfg = runtime.config_manager.parse_rate_limit
    return ParseRecordManager(
        record_file=cfg.record_file,
        same_link_max_count=cfg.same_link.max_count,
        same_link_window_seconds=cfg.same_link.window_seconds,
        same_user_max_count=cfg.same_user.max_count,
        same_user_window_seconds=cfg.same_user.window_seconds,
    )


def _trigger_bilibili_cookie_assist_if_needed(bot: Bot, runtime: MediaParserRuntime) -> None:
    parser = runtime.config_manager.bilibili_parser
    if parser is None:
        return
    reason = parser.consume_assist_request()
    if not reason:
        return
    bili_cfg = runtime.config_manager.bilibili
    bilibili_cookie_assist.trigger_assist_request(
        bot,
        reason=reason,
        auth_runtime=parser.get_auth_runtime(),
        reply_timeout_minutes=bili_cfg.admin_reply_timeout_minutes,
        request_cooldown_minutes=bili_cfg.admin_request_cooldown_minutes,
    )


def _bilibili_cookie_login_runtime() -> tuple[Any, Any] | None:
    cfg = get_config()
    if not cfg.get("enabled", True):
        return None
    try:
        runtime = create_runtime(cfg)
    except Exception as e:
        logger.warning("[MediaParser] Bilibili cookie login runtime init failed: %s", e)
        return None
    parser = runtime.config_manager.bilibili_parser
    if parser is None:
        return None
    bili_cfg = runtime.config_manager.bilibili
    if not bili_cfg.cookie_runtime_enabled:
        return None
    return parser, bili_cfg


class BilibiliCookieAssistReplyHandler:
    """Consume superuser private replies for Bilibili Cookie QR login."""

    name = "BilibiliCookieAssist"

    async def match(self, event: MessageEvent, text: str) -> bool:
        return bilibili_cookie_assist.should_handle_reply(event)

    async def handle(self, bot: Bot, event: MessageEvent) -> None:
        await bilibili_cookie_assist.handle_reply(bot, event)


class AutoMediaParserHandler:
    """Automatically detect and parse supported media platform links."""

    name = "MediaParser"

    async def match(self, event: MessageEvent, text: str) -> bool:
        if bilibili_cookie_assist.should_handle_reply(event):
            return False
        parse_text = _event_text(event)
        lowered = parse_text.casefold()
        if not any(marker in lowered for marker in SUPPORTED_LINK_MARKERS):
            return False
        runtime = _runtime_from_config()
        if runtime is None:
            return False
        if not _scope_allowed(runtime, event):
            return False
        if not runtime.config_manager.trigger.should_parse(parse_text):
            return False
        return bool(runtime.parser_manager.extract_all_links(parse_text))

    async def handle(self, bot: Bot, event: MessageEvent) -> None:
        await _enqueue_text(bot, event, _event_text(event))


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
    await _enqueue_text(ctx.bot, ctx.event, ctx.args, force=True)


@command(
    "B站登录",
    aliases=("B站Cookie", "刷新B站Cookie", "b站登录", "bilibili登录"),
    description="向超级管理员私发 B站扫码登录二维码",
    usage="B站登录",
    show_in_help=False,
)
async def bilibili_cookie_login_command(ctx: CommandContext) -> None:
    if not bilibili_cookie_assist.is_superuser_event(ctx.event):
        await ctx.send(Message(msg("media_parser.bilibili_cookie_assist_permission_denied")))
        return

    runtime_parts = _bilibili_cookie_login_runtime()
    if runtime_parts is None:
        await ctx.send(Message(msg("media_parser.bilibili_cookie_assist_manual_unavailable")))
        return

    parser, bili_cfg = runtime_parts
    started = await bilibili_cookie_assist.start_manual_login(
        ctx.bot,
        auth_runtime=parser.get_auth_runtime(),
        reply_timeout_minutes=bili_cfg.admin_reply_timeout_minutes,
    )
    if started and isinstance(ctx.event, GroupMessageEvent):
        await ctx.send(Message(msg("media_parser.bilibili_cookie_assist_manual_started")))


register_handler(BilibiliCookieAssistReplyHandler())
register_handler(AutoMediaParserHandler())
logger.info("Aggregated media parser registered")
