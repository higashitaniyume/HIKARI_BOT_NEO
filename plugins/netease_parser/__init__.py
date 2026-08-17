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
import time
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
    classify_links,
    extract_all_urls,
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


def _is_mentioned_bot(event: MessageEvent) -> bool:
    """消息是否 @ 了 bot（含 @全体成员）。

    OneBot V11 适配器在事件分发前会把消息开头/结尾的 @bot 段从
    event.message 中移除并置 event.to_me=True，因此优先用 to_me；
    消息中间位置的 @ 段仍保留，遍历段兜底。
    """
    if getattr(event, "to_me", False):
        return True
    self_id = str(getattr(event, "self_id", "") or "")
    for seg in event.message:
        if seg.type == "at":
            qq = seg.data.get("qq", "") if isinstance(seg.data, dict) else ""
            if str(qq) in (self_id, "all"):
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
        messages = resp
    elif isinstance(resp, dict) and isinstance(resp.get("messages"), list):
        messages = resp["messages"]
    else:
        logger.warning("[Netease] 群历史消息响应格式异常 → %r", resp)
        return []
    if messages:
        first = messages[0]
        logger.info(
            "[Netease] 群历史消息 → %d 条, 首条结构 keys=%s message字段类型=%s",
            len(messages),
            list(first.keys()) if isinstance(first, dict) else type(first).__name__,
            type(first.get("message")).__name__ if isinstance(first, dict) else "-",
        )
    return messages


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


def _message_event(message: Message) -> SimpleNamespace:
    """将已解析的 OneBot Message 包装成 parser 可读取的事件对象。"""
    return SimpleNamespace(message=message, get_message=lambda: message)


def _reply_event(event: MessageEvent) -> SimpleNamespace | None:
    """读取 NoneBot 在预处理阶段提取到 event.reply 中的引用消息。"""
    reply = getattr(event, "reply", None)
    message = getattr(reply, "message", None)
    if isinstance(message, Message):
        return _message_event(message)
    return None


def _event_has_netease_link(event: Any) -> bool:
    """事件正文或卡片中是否包含网易云链接。"""
    text = str(event.get_message())
    return has_netease_url(text) or any(
        has_netease_url(url) for url in extract_all_urls(event)
    )


async def _fetch_referenced_message(
    bot: Bot,
    event: MessageEvent,
    message_id: str,
) -> SimpleNamespace | None:
    """按 message_id 精确回查被引用消息（用于「引用卡片 + @bot」解析）。

    优先用 get_msg 直接拉取；失败或返回结构异常时回退
    get_group_msg_history 后按 message_id 精确匹配那一条。

    Returns:
        可复用的提取事件对象（SimpleNamespace），找不到时返回 None。
    """
    mid = str(message_id)

    # NoneBot 的 _check_reply 已调用 get_msg，并把 reply 段从 event.message
    # 移到 event.reply。优先直接复用，避免重复请求及 reply 段丢失。
    reply = getattr(event, "reply", None)
    if reply is not None and str(getattr(reply, "message_id", "")) == mid:
        ref_event = _reply_event(event)
        if ref_event is not None:
            return ref_event

    try:
        resp = await bot.call_api("get_msg", message_id=int(mid))
    except Exception as e:
        logger.warning("[Netease] get_msg 回查失败 → %s", e)
        resp = None

    if isinstance(resp, dict):
        data = resp.get("data") if isinstance(resp.get("data"), dict) else resp
        if isinstance(data.get("message"), list):
            return _history_event(data)

    # 回退：群历史按 message_id 精确匹配
    if isinstance(event, GroupMessageEvent):
        history = await _get_group_history(bot, event, count=30)
        for m in history:
            if str(m.get("message_id", "")) == mid:
                return _history_event(m)
    return None


class AutoNeteaseHandler:
    """
    网易云链接解析 Handler。

    触发规则：
    - 私聊：发送链接或卡片 → 直接解析
    - 群聊：白名单群（auto_parse_groups）→ 发链接/卡片即解析
    - 群聊：其它群 → 手动，仅「@bot + 链接」或「@bot + 引用卡片」解析
    """

    name = "NeteaseParser"

    async def match(self, event: MessageEvent, text: str) -> bool:
        cfg = get_config()
        if not cfg.get("auto_parse", True):
            return False
        if not is_event_allowed(cfg, event):
            return False

        has_link = _event_has_netease_link(event)

        # 私聊：直接解析
        if not isinstance(event, GroupMessageEvent):
            return has_link

        # 群聊：白名单群 → 照常自动解析
        group_id = str(getattr(event, "group_id", "") or "")
        if _is_auto_parse_group(cfg, group_id):
            return has_link

        # 群聊：手动解析，仅「@bot + 链接」或「@bot + 引用卡片」
        if not _is_mentioned_bot(event):
            return False
        if has_link:
            return True
        # @bot 且引用网易云卡片 → 进入 handle 处理；普通回复不抢占。
        ref_event = _reply_event(event)
        return ref_event is not None and _event_has_netease_link(ref_event)

    async def handle(self, bot: Bot, event: MessageEvent) -> None:
        cfg = get_config()
        if not is_event_allowed(cfg, event):
            return

        max_links = max(1, int(cfg.get("max_links_per_message", 5)))
        links = await classify_links(event)
        song_ids = links.song_ids[:max_links]
        program_ids = links.program_ids[:max_links]
        album_ids = links.album_ids[:max_links]
        playlist_ids = links.playlist_ids[:max_links]

        # 群聊 @bot 且自身无链接 → 从引用卡片消息回查
        if isinstance(event, GroupMessageEvent) and not links.any():
            reply_id = _get_reply_message_id(event)
            if reply_id:
                ref_event = await _fetch_referenced_message(bot, event, reply_id)
                if ref_event is not None:
                    ref_links = await classify_links(ref_event)
                    song_ids = ref_links.song_ids[:max_links]
                    program_ids = ref_links.program_ids[:max_links]
                    album_ids = ref_links.album_ids[:max_links]
                    playlist_ids = ref_links.playlist_ids[:max_links]
                    logger.info(
                        "[Netease] 引用卡片回查 → song=%s album=%s playlist=%s program=%s",
                        song_ids, album_ids, playlist_ids, program_ids,
                    )

        # 群聊中专辑/歌单仅提示私聊
        if (album_ids or playlist_ids) and isinstance(event, GroupMessageEvent):
            logger.info("[Netease] 群聊专辑/歌单，提示私聊 → user=%s", event.get_user_id())
            await bot.send(event, Message(msg("netease.private_chat_only")))
            return

        # 音质由用户偏好决定（改音质只走回复换格式）
        quality = "auto"

        # 优先级：歌单 > 专辑 > 单曲/播客
        if playlist_ids:
            for pid in playlist_ids:
                await _enqueue_playlist_parse_job(bot, event, pid, cfg, quality)
            return

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
    reply = getattr(event, "reply", None)
    mid = getattr(reply, "message_id", "") if reply is not None else ""
    if mid:
        return str(mid)
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
    处理回复换格式。

    唯一入口：回复 bot 刚发的网易云消息，内容含 mp3/flac →
    按目标格式重发并更新默认偏好。其它情况（@bot、纯消息、链接消息）不触发。
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
        # 必须回复（引用）某条消息
        if not _get_reply_message_id(event):
            return False
        # NoneBot 会把回复机器人消息标记为 to_me；要求引用来源确实是机器人，
        # 避免回复普通群友时仅说 mp3/flac 也触发偏好修改。
        reply = getattr(event, "reply", None)
        if reply is not None:
            sender = getattr(reply, "sender", None)
            sender_id = getattr(sender, "user_id", None)
            if str(sender_id or "") != str(getattr(event, "self_id", "") or ""):
                return False
        # 含网易云链接 → 交给解析流程
        if has_netease_url(plain):
            return False
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

        # 回复 bot 消息 → 按被回复内容换格式重发
        rec = find_recent_by_message_id(user_id, reply_id)
        if rec is not None:
            if rec.quality == target:
                await bot.send(event, Message(
                    msg("netease.reconvert_same", quality=target.upper()),
                ))
                return
            set_user_quality(user_id, target)
            await bot.send(event, Message(
                msg("netease.pref_updated", quality=target.upper()),
            ))
            await _enqueue_reconvert(bot, event, rec, target)
            return
        # 未命中记录（可能已过太久）：仍记住偏好，并提示直接发链接
        set_user_quality(user_id, target)
        await bot.send(event, Message(
            msg("netease.reconvert_not_found", quality=target.upper()),
        ))


# ── 卡片引导提示 ──

_card_hint_last: dict[str, float] = {}


class NeteaseCardHintHandler:
    """
    群聊网易云卡片引导提示。

    非白名单群、未 @bot 时收到网易云链接（正文或卡片）→ 回一句引导，
    告诉用户「引用卡片 + @bot」即可解析。带同群冷却，避免刷屏。
    """

    name = "NeteaseCardHint"

    async def match(self, event: MessageEvent, text: str) -> bool:
        cfg = get_config()
        hint_cfg = cfg.get("card_hint") if isinstance(cfg.get("card_hint"), dict) else {}
        if not hint_cfg.get("enabled", True):
            return False
        if not is_event_allowed(cfg, event):
            return False
        if not isinstance(event, GroupMessageEvent):
            return False
        if getattr(event, "to_me", False):
            return False
        group_id = str(getattr(event, "group_id", "") or "")
        if _is_auto_parse_group(cfg, group_id):
            return False

        has_link = has_netease_url(text) or any(
            has_netease_url(url) for url in extract_all_urls(event)
        )
        if not has_link:
            return False

        cooldown = max(0.0, float(hint_cfg.get("cooldown_seconds", 300)))
        last = _card_hint_last.get(group_id, 0.0)
        return (time.monotonic() - last) >= cooldown

    async def handle(self, bot: Bot, event: MessageEvent) -> None:
        group_id = str(getattr(event, "group_id", "") or "")
        _card_hint_last[group_id] = time.monotonic()
        await bot.send(event, Message(msg("netease.card_hint")))


# 注册到消息处理管道
register_handler(AutoNeteaseHandler())
register_handler(NeteaseQualityHandler())
register_handler(NeteaseCardHintHandler())
logger.info("网易云音乐解析器已注册 → music.163.com / 163cn.tv")
