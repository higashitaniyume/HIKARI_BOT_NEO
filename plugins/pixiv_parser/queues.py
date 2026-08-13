"""Pixiv 解析队列：把串行下载移出消息处理链。

设计（复用 media_parser/queues.py 已验证的范式）：
- 每个会话（群/私聊）独立队列 + 独立 worker 组 → 一个会话刷屏不会饿死其他会话
- handler 只入队、立刻返回 → 消息链不再被多图串行下载阻塞（不阻塞用户）
- delay_seconds 控制同一会话内两条作品之间的处理间隔，避免请求过快
- 队列关闭时退化回原来的直接处理路径（保持向后兼容）
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from nonebot.adapters.onebot.v11 import Bot, MessageEvent

from core.stats_tracker import increment as stats_increment

from .config import get_config
from .sender import send_artwork

logger = logging.getLogger("HikariBot.PixivQueue")


@dataclass(slots=True)
class PixivQueueItem:
    bot: Bot
    event: MessageEvent
    illust_id: str


_parse_queues: dict[str, asyncio.Queue[PixivQueueItem]] = {}
_parse_worker_sets: dict[str, set[asyncio.Task]] = {}
_parse_queue_init_lock = asyncio.Lock()


def _queue_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    raw = cfg.get("parse_queue") if isinstance(cfg.get("parse_queue"), dict) else {}
    return {
        "enabled": bool(raw.get("enabled", True)),
        "max_size": max(1, int(raw.get("max_size", 50))),
        "max_concurrent": max(1, int(raw.get("max_concurrent", 2))),
        "delay_seconds": max(0.0, float(raw.get("delay_seconds", 1.0))),
    }


def _session_key(event: MessageEvent) -> str:
    return event.get_session_id()


async def enqueue_artworks(bot: Bot, event: MessageEvent, illust_ids: list[str]) -> None:
    """把一批作品 id 放入当前会话的解析队列，立即返回（不阻塞消息链）。"""
    if not illust_ids:
        return
    cfg = get_config()
    settings = _queue_settings(cfg)
    if not settings["enabled"]:
        # 队列关闭：退化回直接串行处理（保持原行为）
        for illust_id in illust_ids:
            await _process_one(bot, event, illust_id)
            if settings["delay_seconds"] > 0:
                await asyncio.sleep(settings["delay_seconds"])
        return

    key = _session_key(event)
    queue = await _ensure_workers(key, settings)
    enqueued = 0
    for illust_id in illust_ids:
        try:
            queue.put_nowait(PixivQueueItem(bot=bot, event=event, illust_id=illust_id))
            enqueued += 1
        except asyncio.QueueFull:
            logger.warning(
                "[Pixiv] 队列已满，丢弃超出部分 → session=%s id=%s",
                key[-32:],
                illust_id,
            )
            break
    logger.info(
        "[Pixiv] 已入队 → session=%s enqueued=%d/%d",
        key[-32:],
        enqueued,
        len(illust_ids),
    )


async def _ensure_workers(key: str, settings: dict[str, Any]) -> asyncio.Queue[PixivQueueItem]:
    """获取（或创建）当前会话的队列与 worker 组。"""
    global _parse_queues, _parse_worker_sets
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
                name=f"HikariPixivParse-{key[-32:]}-{worker_no}",
            )
            _parse_worker_sets[key].add(task)
            task.add_done_callback(lambda t, k=key: _parse_worker_sets.get(k, set()).discard(t))
    return _parse_queues[key]


async def _parse_worker(key: str) -> None:
    """后台 worker：消费当前会话队列中的作品请求。"""
    from core.error_notifier import notify_error_to_superuser, send_user_error

    logger.info("[Pixiv] parse worker started -> key=%s", key[-32:])
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
            await _process_one(item.bot, item.event, item.illust_id, cfg)
            delay = _queue_settings(cfg)["delay_seconds"]
            if delay > 0:
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            queue.task_done()
            break
        except Exception as e:
            logger.exception("[Pixiv] 队列任务处理失败 → id=%s: %s", item.illust_id, e)
            try:
                await send_user_error(item.bot, item.event)
                await notify_error_to_superuser(item.bot, item.event, e, "PixivParser")
            except Exception as notify_err:
                logger.exception("发送错误通知失败: %s", notify_err)
        finally:
            queue.task_done()


async def _process_one(
    bot: Bot,
    event: MessageEvent,
    illust_id: str,
    cfg: dict[str, Any] | None = None,
) -> None:
    """处理单个作品请求（worker 与队列关闭时的直接路径共用）。"""
    from core.error_notifier import notify_error_to_superuser, send_user_error

    if cfg is None:
        cfg = get_config()
    try:
        await send_artwork(bot, event, illust_id, cfg)
        stats_increment(event, "pixiv_parsed", 1)
    except Exception as e:
        logger.exception("[Pixiv] 自动解析失败 → pid=%s: %s", illust_id, e)
        try:
            await send_user_error(bot, event)
            await notify_error_to_superuser(bot, event, e, "PixivParser")
        except Exception as notify_err:
            logger.exception("发送错误通知失败: %s", notify_err)
