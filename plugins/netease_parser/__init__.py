"""
网易云音乐解析插件入口。

NoneBot 加载此插件时自动注册：
1. 自动 URL 检测 handler → 注册到 message_pipeline
2. 检测 music.163.com / 163cn.tv 歌曲链接 → API 获取 FLAC/MP3 → 下载 → 发送

队列行为：多个链接通过 asyncio.Queue 排队，后台 worker 并发处理
（与 media_parser 同样的队列模式）。
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from nonebot.adapters.onebot.v11 import Bot, MessageEvent

from core.access_control import is_event_allowed
from core.error_notifier import notify_error_to_superuser, send_user_error
from core.message_pipeline import register_handler

from .config import get_config
from .parser import (
    extract_album_ids_from_event,
    extract_playlist_ids_from_event,
    extract_program_ids_from_event,
    extract_song_ids_from_event,
    has_netease_url,
)

logger = logging.getLogger("HikariBot.NeteasePlugin")

# 触发首次加载并输出配置摘要
get_config()

# ── 后台队列 ──


@dataclass
class NeteaseQueueItem:
    """单个网易云解析队列条目。"""
    bot: Bot
    event: MessageEvent
    item_id: str
    item_type: str  # "song", "program", 或 "album"


_parse_queue: asyncio.Queue[NeteaseQueueItem] | None = None
_parse_worker_tasks: set[asyncio.Task[None]] = set()


def _queue_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    """从配置中提取队列设置。"""
    raw = cfg.get("parse_queue") if isinstance(cfg.get("parse_queue"), dict) else {}
    return {
        "enabled": bool(raw.get("enabled", True)),
        "max_size": max(1, int(raw.get("max_size", 100))),
        "max_concurrent": max(1, int(raw.get("max_concurrent", 2))),
        "delay_seconds": max(0.0, float(raw.get("delay_seconds", 0.8))),
    }


def _ensure_parse_workers(cfg: dict[str, Any]) -> asyncio.Queue[NeteaseQueueItem]:
    """确保有足够的后台 worker 在运行。"""
    global _parse_queue
    settings = _queue_settings(cfg)
    if _parse_queue is None:
        _parse_queue = asyncio.Queue(maxsize=settings["max_size"])
    alive = {task for task in _parse_worker_tasks if not task.done()}
    _parse_worker_tasks.clear()
    _parse_worker_tasks.update(alive)
    while len(_parse_worker_tasks) < settings["max_concurrent"]:
        worker_no = len(_parse_worker_tasks) + 1
        task = asyncio.create_task(
            _parse_worker(),
            name=f"HikariNeteaseQueue-{worker_no}",
        )
        _parse_worker_tasks.add(task)
        task.add_done_callback(_parse_worker_tasks.discard)
    return _parse_queue


async def _parse_worker() -> None:
    """后台 worker：消费队列中的解析任务。"""
    logger.info("[Netease] 解析队列 worker 已启动")
    while True:
        assert _parse_queue is not None
        item = await _parse_queue.get()
        try:
            cfg = get_config()
            await _process_queue_item(item, cfg)
            delay = _queue_settings(cfg)["delay_seconds"]
            if delay > 0:
                await asyncio.sleep(delay)
        except Exception as e:
            logger.exception("[Netease] 队列任务异常: %s", e)
            try:
                await send_user_error(item.bot, item.event)
                await notify_error_to_superuser(item.bot, item.event, e, "NeteaseParser")
            except Exception as notify_err:
                logger.exception("发送错误通知失败: %s", notify_err)
        finally:
            _parse_queue.task_done()


def _sanitize_filename(text: str) -> str:
    """清理文件名中的非法字符。"""
    return "".join(c for c in text if c.isprintable() and c not in r'<>:"/\|?*').strip()


# 从 processing 模块导入处理函数
from .processing import (  # noqa: E402
    _process_queue_item,
    _process_single_album,
    _process_single_playlist,
    _process_single_program,
    _process_single_song,
)


async def _enqueue_parse_jobs(
    bot: Bot,
    event: MessageEvent,
    song_ids: list[str],
    program_ids: list[str],
) -> None:
    """将歌曲/播客 ID 加入解析队列。"""
    cfg = get_config()
    settings = _queue_settings(cfg)

    # 收集所有条目
    items: list[NeteaseQueueItem] = []
    for pid in program_ids:
        items.append(NeteaseQueueItem(bot=bot, event=event, item_id=pid, item_type="program"))
    for sid in song_ids:
        items.append(NeteaseQueueItem(bot=bot, event=event, item_id=sid, item_type="song"))

    if not items:
        logger.info("[Netease] 未提取到任何歌曲/播客 ID，跳过处理")
        return

    # 队列禁用 → 同步直接处理（用于少量链接）
    if not settings["enabled"]:
        for queued_item in items:
            await _process_queue_item(queued_item, get_config())
        return

    queue = _ensure_parse_workers(cfg)

    queued = 0
    dropped = 0
    for queued_item in items:
        if queue.full():
            dropped += 1
            continue
        queue.put_nowait(queued_item)
        queued += 1

    logger.info(
        "[Netease] 入队完成 → 入队=%d, 丢弃=%d, 队列大小=%d",
        queued, dropped, queue.qsize(),
    )
    if dropped:
        logger.warning("[Netease] 解析队列已满，%d 个链接被丢弃", dropped)


async def _enqueue_album_parse_job(
    bot: Bot,
    event: MessageEvent,
    album_id: str,
    cfg: dict,
) -> None:
    """将专辑 ID 加入解析队列。"""
    settings = _queue_settings(cfg)

    if settings["enabled"]:
        queue = _ensure_parse_workers(cfg)
        item = NeteaseQueueItem(bot=bot, event=event, item_id=album_id, item_type="album")
        if queue.full():
            logger.warning("[Netease] 解析队列已满，专辑 %s 被丢弃", album_id)
            return
        queue.put_nowait(item)
        logger.info("[Netease] 专辑加入解析队列 → id=%s, 队列大小=%d", album_id, queue.qsize())
    else:
        # 队列禁用，直接处理
        await _process_single_album(bot, event, album_id, get_config())


async def _enqueue_playlist_parse_job(
    bot: Bot,
    event: MessageEvent,
    playlist_id: str,
    cfg: dict,
) -> None:
    """将歌单 ID 加入解析队列。"""
    settings = _queue_settings(cfg)

    if settings["enabled"]:
        queue = _ensure_parse_workers(cfg)
        item = NeteaseQueueItem(bot=bot, event=event, item_id=playlist_id, item_type="playlist")
        if queue.full():
            logger.warning("[Netease] 解析队列已满，歌单 %s 被丢弃", playlist_id)
            return
        queue.put_nowait(item)
        logger.info("[Netease] 歌单加入解析队列 → id=%s, 队列大小=%d", playlist_id, queue.qsize())
    else:
        await _process_single_playlist(bot, event, playlist_id, get_config())


class AutoNeteaseHandler:
    """自动检测网易云音乐歌曲链接并解析的 Handler。"""

    name = "NeteaseParser"

    async def match(self, event: MessageEvent, text: str) -> bool:
        cfg = get_config()
        if not cfg.get("auto_parse", True):
            logger.debug("[Netease] match ✗ auto_parse=False, 跳过")
            return False
        if not is_event_allowed(cfg, event):
            logger.debug("[Netease] match ✗ 权限限制 user=%s", event.get_user_id())
            return False

        # 检查正文是否包含网易云链接
        if has_netease_url(text):
            logger.debug("[Netease] match ✓ 正文命中 → text=%s...", text[:80])
            return True

        # 检查 QQ 卡片元数据是否包含网易云链接
        from .parser import extract_all_urls

        card_urls = extract_all_urls(event)
        if card_urls:
            logger.debug("[Netease] match 从卡片提取到 %d 个 URL: %s", len(card_urls), card_urls)
            for url in card_urls:
                if has_netease_url(url):
                    logger.debug(
                        "[Netease] match ✓ 卡片元数据命中 → url=%s", url[:80],
                    )
                    return True
                logger.debug("[Netease] match   URL 非网易云: %s", url[:80])
        else:
            logger.debug("[Netease] match 卡片未提取到任何 URL")

        return False

    async def handle(self, bot: Bot, event: MessageEvent) -> None:
        cfg = get_config()
        if not is_event_allowed(cfg, event):
            return

        max_links = max(1, int(cfg.get("max_links_per_message", 5)))
        program_ids = (await extract_program_ids_from_event(event))[:max_links]
        song_ids = (await extract_song_ids_from_event(event))[:max_links]
        album_ids = (await extract_album_ids_from_event(event))[:max_links]
        playlist_ids = (await extract_playlist_ids_from_event(event))[:max_links]

        # 优先级：歌单 > 专辑 > 单曲/播客
        if playlist_ids:
            for pid in playlist_ids:
                await _enqueue_playlist_parse_job(bot, event, pid, cfg)
            return

        # 专辑优先：如果有专辑链接，将专辑歌曲入队
        if album_ids:
            for album_id in album_ids:
                await _enqueue_album_parse_job(bot, event, album_id, cfg)
            return

        await _enqueue_parse_jobs(bot, event, song_ids, program_ids)


# 注册到消息处理管道
register_handler(AutoNeteaseHandler())
logger.info("网易云音乐解析器已注册 → music.163.com / 163cn.tv")
