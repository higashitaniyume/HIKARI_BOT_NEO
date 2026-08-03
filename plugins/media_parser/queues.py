"""Queue management for the aggregated media parser."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent

from core.bot_messages import get_message as msg
from core.error_notifier import notify_superuser_message
from core.stats_tracker import increment as stats_increment
from core.activity_tracker import QUEUE_SIZES
from third_party.astrbot_plugin_media_parser.core.storage.parse_record import ParseRecordManager

from .bilibili_cookie_assist import bilibili_cookie_assist
from .config import get_config
from .runtime import MediaParserRuntime, create_runtime
from .sender import send_metadata_result

logger = logging.getLogger("HikariBot.MediaParser")

# Per-conversation parse queues — one conversation cannot starve another
_parse_queues: dict[str, asyncio.Queue["MediaParseQueueItem"]] = {}
_parse_worker_sets: dict[str, set[asyncio.Task]] = {}
_parse_queue_init_lock = asyncio.Lock()

# Per-conversation send queues (bounded — backpressure, not throttling)
_send_queues: dict[str, asyncio.Queue["MediaSendQueueItem"]] = {}
_send_worker_tasks: dict[str, asyncio.Task[None]] = {}

# Cached runtime + aiohttp session (recreated on config file change)
_runtime_cache: MediaParserRuntime | None = None
_runtime_cache_mtime: float = 0.0
_runtime_cache_size: int = 0
_runtime_cache_path = Path("BotData/plugin_configs/media_parser.json")
_session_cache: aiohttp.ClientSession | None = None


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


def _get_runtime() -> MediaParserRuntime | None:
    """Return cached runtime, recreating when config file changes."""
    global _runtime_cache, _runtime_cache_mtime, _runtime_cache_size

    try:
        stat = _runtime_cache_path.stat()
        mtime = stat.st_mtime
        size = stat.st_size
    except OSError:
        mtime = 0.0
        size = 0

    if _runtime_cache is not None and mtime == _runtime_cache_mtime and size == _runtime_cache_size:
        return _runtime_cache

    # Config changed (or first load) -> rebuild runtime
    cfg = get_config()
    if not cfg.get("enabled", True):
        _runtime_cache = None
        return None
    try:
        runtime = create_runtime(cfg)
    except Exception as e:
        logger.warning("[MediaParser] runtime init skipped: %s", e)
        _runtime_cache = None
        return None
    if not runtime.config_manager.parser_output.has_any_output():
        _runtime_cache = None
        return None

    _runtime_cache = runtime
    _runtime_cache_mtime = mtime
    _runtime_cache_size = size
    return _runtime_cache


async def _get_session() -> aiohttp.ClientSession:
    """Return the cached aiohttp session, creating on demand."""
    global _session_cache
    if _session_cache is None or _session_cache.closed:
        _session_cache = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=120),
        )
    return _session_cache


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
        "delay_403_base": max(0.0, float(cfg.get("parse_retry_403_delay_base", 3.0))),
    }


async def _ensure_parse_workers(key: str, cfg: dict[str, Any]) -> asyncio.Queue[MediaParseQueueItem]:
    """Get or create per-conversation parse queue + workers."""
    global _parse_queues, _parse_worker_sets
    settings = _queue_settings(cfg)

    async with _parse_queue_init_lock:
        if key not in _parse_queues:
            _parse_queues[key] = asyncio.Queue(maxsize=settings["max_size"])
        if key not in _parse_worker_sets:
            _parse_worker_sets[key] = set()

        alive = {t for t in _parse_worker_sets[key] if not t.done()}
        _parse_worker_sets[key].clear()
        _parse_worker_sets[key].update(alive)
        while len(_parse_worker_sets[key]) < settings["max_concurrent"]:
            worker_no = len(_parse_worker_sets[key]) + 1
            task = asyncio.create_task(
                _parse_worker(key),
                name=f"HikariMediaParserParse-{key[-32:]}-{worker_no}",
            )
            _parse_worker_sets[key].add(task)
            task.add_done_callback(lambda t, k=key: _parse_worker_sets.get(k, set()).discard(t))

    return _parse_queues[key]


async def _parse_worker(key: str) -> None:
    """Background worker: consume parse queue for one conversation."""
    from core.error_notifier import notify_error_to_superuser, send_user_error
    from .prepare import _process_parse_item

    logger.info("[MediaParser] parse worker started -> key=%s", key)
    while True:
        try:
            queue = _parse_queues.get(key)
            if queue is None:
                await asyncio.sleep(0.5)
                continue
            item = await queue.get()
        except asyncio.CancelledError:
            break

        try:
            cfg = get_config()
            await _process_parse_item(item)
            delay = _queue_settings(cfg)["delay_seconds"]
            if delay > 0:
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            queue.task_done()
            break
        except Exception as e:
            logger.exception("[MediaParser] queued parse failed: %s", e)
            try:
                await send_user_error(item.bot, item.event)
                await notify_error_to_superuser(item.bot, item.event, e, "MediaParser")
            except Exception as notify_err:
                logger.exception("发送错误通知失败: %s", notify_err)
        finally:
            queue.task_done()


async def _enqueue_text(bot: Bot, event: MessageEvent, text: str, *, force: bool = False) -> None:
    from .prepare import _process_parse_item, dedupe_links, is_platform_allowed

    runtime = _get_runtime()
    if runtime is None:
        return
    if not force and not runtime.config_manager.trigger.should_parse(text):
        return

    links = runtime.parser_manager.extract_all_links(text)
    links = dedupe_links(links)
    links = [
        (url, parser) for url, parser in links
        if is_platform_allowed(getattr(parser, "name", "unknown"), event)
    ]
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

    key = _conversation_key(bot, event)
    queue = await _ensure_parse_workers(key, runtime.config)
    queued = 0
    for link_item in links:
        await queue.put(MediaParseQueueItem(
            bot=bot,
            event=event,
            text=link_item[0],
            links_with_parser=[link_item],
            force=force,
        ))
        queued += 1
    logger.info(
        "[MediaParser] queued parse jobs -> key=%s, count=%d, queue_size=%d",
        key, queued, queue.qsize(),
    )
    QUEUE_SIZES["media_parser_parse"] = queue.qsize()


def _conversation_key(bot: Bot, event: MessageEvent) -> str:
    bot_id = getattr(bot, "self_id", "bot")
    if isinstance(event, GroupMessageEvent):
        return f"{bot_id}:group:{event.group_id}"
    return f"{bot_id}:private:{event.get_user_id()}"


def _ensure_send_worker(key: str) -> asyncio.Queue[MediaSendQueueItem]:
    queue = _send_queues.get(key)
    if queue is None:
        queue = asyncio.Queue(maxsize=200)
        _send_queues[key] = queue
    task = _send_worker_tasks.get(key)
    if task is None or task.done():
        task = asyncio.create_task(_send_worker(key), name=f"HikariMediaParserSendQueue-{key[-48:]}")
        _send_worker_tasks[key] = task
        task.add_done_callback(lambda done_task: _clear_send_worker(key, done_task))
    return queue


def _clear_send_worker(key: str, task: asyncio.Task[None]) -> None:
    if _send_worker_tasks.get(key) is task:
        _send_worker_tasks.pop(key, None)


async def _enqueue_send(item: MediaSendQueueItem) -> None:
    key = _conversation_key(item.bot, item.event)
    queue = _ensure_send_worker(key)
    await queue.put(item)
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
        try:
            queue = _send_queues.get(key)
            if queue is None:
                await asyncio.sleep(1)
                continue
            item = await queue.get()
        except asyncio.CancelledError:
            break

        try:
            await _send_processed_item(item)
        except asyncio.CancelledError:
            queue.task_done()
            break
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

    if total_sent > 0:
        stats_increment(item.event, "media_parser_parsed", len(item.processed))
        return

    # All attempts failed — check if we have error metadata to report
    errors = [
        m for m in item.processed
        if m.get("error") and m.get("_enable_text_metadata")
    ]
    if errors:
        err = errors[0]
        platform = err.get("platform") or err.get("parser_name") or "unknown"
        raw_reason = str(err.get("error", ""))
        # Translate known transient errors to user-friendly message
        if any(p in raw_reason for p in ("-404", "啥都木有", "view error")):
            reason = "抖音内容暂时不可访问，可能是链接失效或触发了平台限流，请稍后重试"
        else:
            reason = f"解析失败：{raw_reason[:120]}"
        logger.info(
            "[MediaParser] parse failed -> platform=%s url=%s error=%s (notified superuser only)",
            platform,
            err.get("source_url", ""),
            raw_reason[:120],
        )
        # 失败详情只私发 superuser，不在群里暴露
        await notify_superuser_message(
            item.bot,
            msg("media_parser.metadata_error", platform=platform, reason=reason, url=err.get("source_url", "")),
        )
    elif not any(m.get("_enable_text_metadata") for m in item.processed):
        await item.bot.send(item.event, Message(msg("media_parser.no_media")))
    stats_increment(item.event, "media_parser_parsed", len(item.processed))


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
    runtime = _get_runtime()
    if runtime is None:
        return None
    parser = runtime.config_manager.bilibili_parser
    if parser is None:
        return None
    bili_cfg = runtime.config_manager.bilibili
    if not bili_cfg.cookie_runtime_enabled:
        return None
    return parser, bili_cfg
