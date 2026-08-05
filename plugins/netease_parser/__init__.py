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
import re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, GroupMessageEvent

from core.access_control import is_event_allowed
from core.bot_messages import get_message as msg
from core.error_notifier import notify_error_to_superuser, send_user_error
from core.message_pipeline import register_handler

from .config import get_config
from .parser import (
    extract_album_ids_from_event,
    extract_all_urls,
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
    quality: str = "auto"  # "auto" = 按发送者偏好, "mp3"/"flac" = 指定格式


_parse_queue: asyncio.Queue[NeteaseQueueItem] | None = None
_parse_worker_tasks: set[asyncio.Task[None]] = set()


def _queue_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    """从配置中提取队列设置。"""
    raw = cfg.get("parse_queue") if isinstance(cfg.get("parse_queue"), dict) else {}
    return {
        "enabled": bool(raw.get("enabled", True)),
        "max_size": max(1, int(raw.get("max_size", 100))),
        "max_concurrent": max(1, int(raw.get("max_concurrent", 4))),
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
    quality: str = "auto",
) -> None:
    """将歌曲/播客 ID 加入解析队列。"""
    cfg = get_config()
    settings = _queue_settings(cfg)

    # 收集所有条目
    items: list[NeteaseQueueItem] = []
    for pid in program_ids:
        items.append(NeteaseQueueItem(bot=bot, event=event, item_id=pid, item_type="program", quality=quality))
    for sid in song_ids:
        items.append(NeteaseQueueItem(bot=bot, event=event, item_id=sid, item_type="song", quality=quality))

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
    quality: str = "auto",
) -> None:
    """将专辑 ID 加入解析队列。"""
    settings = _queue_settings(cfg)

    if settings["enabled"]:
        queue = _ensure_parse_workers(cfg)
        item = NeteaseQueueItem(bot=bot, event=event, item_id=album_id, item_type="album", quality=quality)
        if queue.full():
            logger.warning("[Netease] 解析队列已满，专辑 %s 被丢弃", album_id)
            return
        queue.put_nowait(item)
        logger.info("[Netease] 专辑加入解析队列 → id=%s, quality=%s, 队列大小=%d", album_id, quality, queue.qsize())
    else:
        # 队列禁用，直接处理
        await _process_single_album(bot, event, album_id, get_config(), quality)


async def _enqueue_playlist_parse_job(
    bot: Bot,
    event: MessageEvent,
    playlist_id: str,
    cfg: dict,
    quality: str = "auto",
) -> None:
    """将歌单 ID 加入解析队列。"""
    settings = _queue_settings(cfg)

    if settings["enabled"]:
        queue = _ensure_parse_workers(cfg)
        item = NeteaseQueueItem(bot=bot, event=event, item_id=playlist_id, item_type="playlist", quality=quality)
        if queue.full():
            logger.warning("[Netease] 解析队列已满，歌单 %s 被丢弃", playlist_id)
            return
        queue.put_nowait(item)
        logger.info("[Netease] 歌单加入解析队列 → id=%s, quality=%s, 队列大小=%d", playlist_id, quality, queue.qsize())
    else:
        await _process_single_playlist(bot, event, playlist_id, get_config(), quality)


def _is_auto_parse_group(cfg: dict, group_id: str) -> bool:
    """该群是否为管理员配置的自动解析群。

    默认群聊为手动解析（仅被@bot 触发）；只有启用 auto_parse_groups 且
    群号在列表内的群才会自动解析链接。
    """
    auto = cfg.get("auto_parse_groups") if isinstance(cfg.get("auto_parse_groups"), dict) else {}
    if not auto.get("enable", False):
        return False
    groups = [str(g) for g in auto.get("groups", []) if str(g)]
    return str(group_id) in groups


def _is_mentioned_bot(event: MessageEvent, bot_self_id: str) -> bool:
    """消息是否 @ 了 bot（含 @全体成员）。"""
    for seg in event.message:
        if seg.type == "at":
            qq = seg.data.get("qq", "") if isinstance(seg.data, dict) else ""
            if str(qq) in (bot_self_id, "all"):
                return True
    return False


async def _get_group_history(
    bot: Bot,
    event: GroupMessageEvent,
    count: int = 20,
) -> list[dict]:
    """获取群最近消息（从最新向前 count 条），返回消息 dict 列表。"""
    try:
        resp = await bot.call_api(
            "get_group_msg_history", group_id=event.group_id, count=count,
        )
    except Exception as e:
        logger.warning("[Netease] 获取群历史消息失败 → %s", e)
        return []
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        messages = resp.get("messages")
        if isinstance(messages, list):
            return messages
    logger.warning("[Netease] 群历史消息响应格式异常 → %r", resp)
    return []


def _history_event(message: dict) -> SimpleNamespace:
    """历史消息 dict → 可复用的提取事件对象（复用 parser 的提取函数）。"""
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    segments = []
    raw = message.get("message") if isinstance(message, dict) else None
    if isinstance(raw, list):
        for seg in raw:
            try:
                segments.append(
                    MessageSegment(type=seg["type"], data=seg.get("data", {}) or {}),
                )
            except Exception:
                continue
    msg = Message(segments)
    return SimpleNamespace(message=msg, get_message=lambda: msg)


def _history_before_trigger(
    history: list[dict],
    event: MessageEvent,
    limit: int = 10,
) -> list[dict]:
    """取被@消息之前的最近 limit 条历史消息（按 time 升序）。"""
    trigger_time = float(getattr(event, "time", 0) or 0)
    trigger_id = str(getattr(event, "message_id", "") or "")
    items = sorted(history, key=lambda m: float(m.get("time", 0) or 0))

    # 定位被@消息：优先按 message_id，其次按 time
    idx = None
    for i, m in enumerate(items):
        if trigger_id and str(m.get("message_id", "")) == trigger_id:
            idx = i
            break
    if idx is None and trigger_time > 0:
        for i, m in enumerate(items):
            if float(m.get("time", 0) or 0) >= trigger_time:
                idx = i
                break
    if idx is None:
        # 完全定位不到被@消息 → 取历史中最早的 limit 条（最贴近“之前”方向）
        return items[:limit]
    return items[max(0, idx - limit):idx]


def _has_netease_in_history(history: list[dict], event: MessageEvent) -> bool:
    """被@消息之前 10 条历史中是否含网易云链接（正文 URL 或卡片）。"""
    for item in _history_before_trigger(history, event):
        try:
            text = str(item.get("message", ""))
            if has_netease_url(text):
                return True
            for url in extract_all_urls(_history_event(item)):
                if has_netease_url(url):
                    return True
        except Exception:
            continue
    return False


class AutoNeteaseHandler:
    """
    网易云链接解析 Handler。

    触发规则：
    - 私聊：发送链接或小卡片 → 直接解析
    - 群聊：默认手动解析 —— 仅在被@bot 时解析被@消息自身或它之前 10 条
      消息内的网易云链接/卡片；管理员把群配置进 auto_parse_groups 后，
      该群恢复自动解析
    """

    name = "NeteaseParser"

    async def match(self, event: MessageEvent, text: str) -> bool:
        cfg = get_config()
        if not cfg.get("auto_parse", True):
            logger.debug("[Netease] match ✗ auto_parse=False, 跳过")
            return False
        if not is_event_allowed(cfg, event):
            logger.debug("[Netease] match ✗ 权限限制 user=%s", event.get_user_id())
            return False

        # 检查正文与卡片是否包含网易云链接
        has_self_link = has_netease_url(text)
        if not has_self_link:
            card_urls = extract_all_urls(event)
            has_self_link = any(has_netease_url(url) for url in card_urls)

        # 私聊：直接解析
        if not isinstance(event, GroupMessageEvent):
            return has_self_link

        # 群聊：管理员配置的自动解析群 → 照常自动解析
        group_id = str(getattr(event, "group_id", "") or "")
        if _is_auto_parse_group(cfg, group_id):
            return has_self_link

        # 默认手动解析群：仅被@时解析（自身链接 或 之前 10 条历史）
        try:
            from nonebot import get_bot

            bot = get_bot()
            bot_self_id = str(bot.self_id or "")
        except Exception:
            return False
        if not _is_mentioned_bot(event, bot_self_id):
            logger.debug("[Netease] match ✗ 未被@ group=%s", group_id)
            return False

        if has_self_link:
            logger.debug("[Netease] match ✓ 群聊被@且自身含链接 group=%s", group_id)
            return True

        # 被@消息自身无链接 → 查之前 10 条历史
        history = await _get_group_history(bot, event)
        if _has_netease_in_history(history, event):
            logger.info("[Netease] match ✓ 群聊被@，之前 10 条内发现网易云链接 group=%s", group_id)
            return True
        logger.debug("[Netease] match ✗ 群聊被@但历史无网易云链接 group=%s", group_id)
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

        # 群聊被@且自身无链接 → 从之前 10 条历史消息中提取（match 已确认历史有链接）
        if (
            isinstance(event, GroupMessageEvent)
            and not (playlist_ids or album_ids or song_ids or program_ids)
        ):
            history = await _get_group_history(bot, event)
            for item in _history_before_trigger(history, event):
                try:
                    h_event = _history_event(item)
                except Exception:
                    continue
                program_ids.extend(await extract_program_ids_from_event(h_event))
                song_ids.extend(await extract_song_ids_from_event(h_event))
                album_ids.extend(await extract_album_ids_from_event(h_event))
                playlist_ids.extend(await extract_playlist_ids_from_event(h_event))

            def _dedup(ids: list[str]) -> list[str]:
                return list(dict.fromkeys(ids))[:max_links]

            program_ids = _dedup(program_ids)
            song_ids = _dedup(song_ids)
            album_ids = _dedup(album_ids)
            playlist_ids = _dedup(playlist_ids)
            logger.info(
                "[Netease] 历史提取完成 → song=%s album=%s playlist=%s program=%s",
                song_ids, album_ids, playlist_ids, program_ids,
            )

        # 群聊中专辑/歌单仅提示私聊
        if (album_ids or playlist_ids) and isinstance(event, GroupMessageEvent):
            logger.info("[Netease] 群聊专辑/歌单，提示私聊 → user=%s", event.get_user_id())
            await bot.send(event, Message(msg("netease.private_chat_only")))
            return

        # 链接消息内带 mp3/flac 字样 → 本次解析按指定格式（覆盖用户偏好）
        plain = _plain_text(event)
        has_mp3 = bool(_MP3_RE.search(plain))
        has_flac = bool(_FLAC_RE.search(plain))
        if has_mp3 and not has_flac:
            quality = "mp3"
        elif has_flac and not has_mp3:
            quality = "flac"
        else:
            quality = "auto"
        if quality != "auto":
            logger.info("[Netease] 链接消息指定格式 → quality=%s user=%s", quality, event.get_user_id())

        # 优先级：歌单 > 专辑 > 单曲/播客
        if playlist_ids:
            for pid in playlist_ids:
                await _enqueue_playlist_parse_job(bot, event, pid, cfg, quality)
            return

        # 专辑优先：如果有专辑链接，将专辑歌曲入队
        if album_ids:
            for album_id in album_ids:
                await _enqueue_album_parse_job(bot, event, album_id, cfg, quality)
            return

        await _enqueue_parse_jobs(bot, event, song_ids, program_ids, quality)


# ── 格式偏好声明 / 回复换格式 ──

_MP3_RE = re.compile(r"(?<![a-z])mp3(?![a-z])", re.I)
_FLAC_RE = re.compile(r"(?<![a-z])flac(?![a-z])", re.I)


def _plain_text(event: MessageEvent) -> str:
    """提取消息中的纯文本（跳过回复/图片/卡片等非文本段）。"""
    parts = []
    for seg in event.message:
        if seg.type == "text":
            parts.append(str(seg.data.get("text", "")))
    return "".join(parts)


def _get_reply_message_id(event: MessageEvent) -> str:
    """获取消息引用的回复目标 message_id（无回复时返回空串）。"""
    for seg in event.message:
        if seg.type == "reply":
            mid = seg.data.get("id", "") if isinstance(seg.data, dict) else ""
            return str(mid or "")
    return ""


async def _enqueue_reconvert(
    bot: Bot,
    event: MessageEvent,
    rec: "SentRecord",
    target: str,
) -> None:
    """按最近发送记录以目标格式重发（单曲/播客/专辑/歌单）。"""
    cfg = get_config()
    if rec.item_type == "song":
        await _enqueue_parse_jobs(bot, event, [rec.item_id], [], quality=target)
    elif rec.item_type == "program":
        await _enqueue_parse_jobs(bot, event, [], [rec.item_id], quality=target)
    elif rec.item_type == "album":
        await _enqueue_album_parse_job(bot, event, rec.item_id, cfg, quality=target)
    elif rec.item_type == "playlist":
        await _enqueue_playlist_parse_job(bot, event, rec.item_id, cfg, quality=target)
    else:
        logger.warning("[Netease] 未知发送记录类型，无法重发 → type=%s", rec.item_type)


class NeteaseQualityHandler:
    """
    处理格式偏好声明与回复换格式。

    - 回复 bot 刚发的网易云消息，内容含 mp3/flac → 按目标格式重发（并记住偏好）
    - 普通消息（无链接）含 mp3/flac 字样 → 记住偏好，之后解析默认按偏好
    - 含网易云链接的消息交给 AutoNeteaseHandler（链接消息内带 mp3/flac 由解析流程处理）
    """

    name = "NeteaseQuality"

    async def match(self, event: MessageEvent, text: str) -> bool:
        cfg = get_config()
        if not cfg.get("quality_switch", True):
            return False
        if not is_event_allowed(cfg, event):
            return False

        plain = _plain_text(event)
        if not _MP3_RE.search(plain) and not _FLAC_RE.search(plain):
            return False
        # 含网易云链接（正文或卡片）→ 交给解析流程处理
        if has_netease_url(plain):
            return False
        from .parser import extract_all_urls

        if extract_all_urls(event):
            return False
        return True

    async def handle(self, bot: Bot, event: MessageEvent) -> None:
        from .prefs import (
            find_recent_by_message_id,
            set_user_quality,
        )

        plain = _plain_text(event)
        if _FLAC_RE.search(plain) and not _MP3_RE.search(plain):
            target = "flac"
        else:
            target = "mp3"
        user_id = event.get_user_id()
        reply_id = _get_reply_message_id(event)
        logger.info(
            "[Netease] 格式指令 → target=%s user=%s reply=%s text=%r",
            target, user_id, reply_id or "-", plain[:50],
        )

        if reply_id:
            # 回复 bot 消息 → 按被回复内容换格式重发
            rec = find_recent_by_message_id(user_id, reply_id)
            if rec is not None:
                if rec.quality == target:
                    await bot.send(event, Message(
                        msg("netease.reconvert_same", quality=target.upper()),
                    ))
                    return
                set_user_quality(user_id, target)
                await _enqueue_reconvert(bot, event, rec, target)
                return
            # 未命中记录（可能已过太久）：仍记住偏好，并提示直接发链接
            set_user_quality(user_id, target)
            await bot.send(event, Message(
                msg("netease.reconvert_not_found", quality=target.upper()),
            ))
            return

        # 非回复消息 → 偏好声明
        set_user_quality(user_id, target)
        key = "netease.pref_set_mp3" if target == "mp3" else "netease.pref_set_flac"
        await bot.send(event, Message(msg(key)))


# 注册到消息处理管道
register_handler(AutoNeteaseHandler())
register_handler(NeteaseQualityHandler())
logger.info("网易云音乐解析器已注册 → music.163.com / 163cn.tv")
