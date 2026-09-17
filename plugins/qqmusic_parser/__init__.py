"""
QQ 音乐解析插件入口。

NoneBot 加载此插件时注册两个 handler：

1. **AutoQQMusicHandler** —— 自动解析。
   私聊直接解析；群聊默认只响应「@bot + 链接」或「@bot + 引用卡片」，
   只有配置在 ``auto_parse_groups`` 白名单里的群才自动解析（与 ``netease_parser``
   一致，避免群里的音乐卡片被无脑下载）。
2. **QQMusicCardHintHandler** —— 群聊卡片引导。
   非白名单群、未 @bot 时收到 QQ 音乐卡片，回一句引导（同群冷却），
   否则用户会以为机器人坏了。

下载用 yt-dlp 的 ``qqmusic`` 提取器（不做转码，保原始音质档）。
匿名请求只能拿到 128k MP3 / 96k / 48k AAC；在 ``BotData/cookies/qqmusic.txt``
放一个登录后的 Netscape cookie 文件即可解锁 320k MP3 与无损 FLAC。
"""

from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace
from typing import Any

from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
)

from core.access_control import is_event_allowed
from core.activity_tracker import ActivityScope
from core.bot_messages import get_message as msg
from core.error_notifier import notify_error_to_superuser, send_user_error
from core.message_pipeline import register_handler
from core.stats_tracker import increment as stats_increment

from .config import get_config
from .errors import QQMusicError
from .parser import collect_song_refs
from .processing import process_song, user_message_for_error

logger = logging.getLogger("HikariBot.QQMusicPlugin")

# 触发首次加载并输出配置摘要
get_config()


# ── 触发判定辅助 ──


def _event_has_qqmusic(event: Any) -> bool:
    """事件正文或卡片里是否含 QQ 音乐歌曲引用。"""
    return bool(collect_song_refs(event))


def _is_auto_parse_group(cfg: dict[str, Any], group_id: str) -> bool:
    """该群是否为管理员配置的自动解析群。"""
    auto = cfg.get("auto_parse_groups") if isinstance(cfg.get("auto_parse_groups"), dict) else {}
    if not auto.get("enable", False):
        return False
    groups = [str(g) for g in auto.get("groups", []) if str(g)]
    return str(group_id) in groups


def _is_mentioned_bot(event: MessageEvent) -> bool:
    """消息是否 @ 了 bot（含 @全体成员）。

    OneBot V11 适配器在事件分发前会把消息开头/结尾的 @bot 段从
    ``event.message`` 中移除并置 ``event.to_me=True``，因此优先用 to_me；
    消息中间位置的 @ 段仍保留，遍历段兜底。
    """
    if getattr(event, "to_me", False):
        return True
    self_id = str(getattr(event, "self_id", "") or "")
    for segment in event.message:
        if segment.type == "at":
            qq = segment.data.get("qq", "") if isinstance(segment.data, dict) else ""
            if str(qq) in (self_id, "all"):
                return True
    return False


def _reply_event(event: MessageEvent) -> SimpleNamespace | None:
    """把「引用的消息」包装成 parser 可读取的事件对象。"""
    reply = getattr(event, "reply", None)
    message = getattr(reply, "message", None)
    if isinstance(message, Message):
        return SimpleNamespace(message=message, get_message=lambda: message)
    return None


# ── 自动解析 ──


class AutoQQMusicHandler:
    """QQ 音乐链接/卡片自动解析 Handler。"""

    name = "QQMusicParser"

    async def match(self, event: MessageEvent, text: str) -> bool:
        cfg = get_config()
        if not cfg.get("enabled", True) or not cfg.get("auto_parse", True):
            return False
        if not is_event_allowed(cfg, event):
            return False

        has_link = _event_has_qqmusic(event)

        # 私聊：直接解析
        if not isinstance(event, GroupMessageEvent):
            return has_link

        group_id = str(getattr(event, "group_id", "") or "")
        if _is_auto_parse_group(cfg, group_id):
            return has_link

        # 群聊：手动解析，仅「@bot + 链接」或「@bot + 引用卡片」
        if not _is_mentioned_bot(event):
            return False
        if has_link:
            return True
        ref_event = _reply_event(event)
        return ref_event is not None and _event_has_qqmusic(ref_event)

    async def handle(self, bot: Bot, event: MessageEvent) -> None:
        cfg = get_config()
        if not is_event_allowed(cfg, event):
            return

        max_links = max(1, int(cfg.get("max_links_per_message", 3)))
        refs = collect_song_refs(event)[:max_links]

        # 群聊 @bot 且自身无链接 → 从引用消息回查
        if not refs and isinstance(event, GroupMessageEvent):
            ref_event = _reply_event(event)
            if ref_event is not None:
                refs = collect_song_refs(ref_event)[:max_links]
                logger.info(
                    "[QQMusic] 引用卡片回查 → %s",
                    [ref.key for ref in refs],
                )

        if not refs:
            logger.info("[QQMusic] 未提取到歌曲引用，跳过 → user=%s", event.get_user_id())
            return

        # 群聊多链接仅提示私聊
        if len(refs) > 1 and isinstance(event, GroupMessageEvent):
            logger.info(
                "[QQMusic] 群聊多链接，提示私聊 → user=%s, count=%d",
                event.get_user_id(), len(refs),
            )
            await bot.send(event, Message(msg("qqmusic.private_chat_only")))
            return

        logger.info(
            "[QQMusic] 解析触发 → user=%s, 引用数=%d",
            event.get_user_id(), len(refs),
        )

        for index, ref in enumerate(refs, start=1):
            logger.info(
                "[QQMusic] 处理 %d/%d → %s (%s)",
                index, len(refs), ref.key,
                "songid" if ref.songid else "songmid",
            )
            try:
                with ActivityScope(
                    "qqmusic_parser", "downloading", "下载 QQ 音乐", description=ref.key,
                ):
                    await process_song(bot, event, ref, cfg)
                stats_increment(event, "qqmusic_downloaded", 1)
            except QQMusicError as exc:
                logger.warning("[QQMusic] 处理失败 → %s: %s", ref.key, exc)
                await bot.send(event, Message(user_message_for_error(exc, cfg)))
            except Exception as exc:  # noqa: BLE001 - 兜底，避免整条消息链崩掉
                logger.exception("[QQMusic] 未预期异常 → %s: %s", ref.key, exc)
                try:
                    await send_user_error(bot, event)
                    await notify_error_to_superuser(bot, event, exc, "QQMusicParser")
                except Exception as notify_err:
                    logger.exception("发送错误通知失败: %s", notify_err)

            if index < len(refs):
                await asyncio.sleep(1.0)


# ── 群聊卡片引导 ──

_card_hint_last: dict[str, float] = {}


class QQMusicCardHintHandler:
    """非白名单群、未 @bot 时收到 QQ 音乐链接 → 回一句引导（带同群冷却）。"""

    name = "QQMusicCardHint"

    async def match(self, event: MessageEvent, text: str) -> bool:
        cfg = get_config()
        if not cfg.get("enabled", True):
            return False
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
        if not _event_has_qqmusic(event):
            return False

        cooldown = max(0.0, float(hint_cfg.get("cooldown_seconds", 300)))
        return (time.monotonic() - _card_hint_last.get(group_id, 0.0)) >= cooldown

    async def handle(self, bot: Bot, event: MessageEvent) -> None:
        group_id = str(getattr(event, "group_id", "") or "")
        _card_hint_last[group_id] = time.monotonic()
        await bot.send(event, Message(msg("qqmusic.card_hint")))


register_handler(AutoQQMusicHandler())
register_handler(QQMusicCardHintHandler())
logger.info("QQ 音乐解析器已注册 → y.qq.com / i.y.qq.com（yt-dlp qqmusic）")
