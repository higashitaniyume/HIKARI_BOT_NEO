"""Auto-accept friend requests, send a welcome message, and notify the superuser on friend add."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from nonebot import on_notice, on_request
from nonebot.adapters.onebot.v11 import Bot, FriendAddNoticeEvent, FriendRequestEvent
from nonebot.adapters.onebot.v11.exception import ActionFailed

from core.bot_messages import get_message
from core.config_loader import load_main_config
from core.lifecycle_logging import describe_event
from plugins.push_framework import PushContext, register_push_source, run_jobs_by_source

from .config import get_config

logger = logging.getLogger("HikariBot.FriendManager")

friend_request_matcher = on_request(priority=1, block=False)
friend_add_matcher = on_notice(priority=1, block=False)

logger.info("[FriendManager] 好友管理插件已加载")


# ── 好友请求 ──────────────────────────────────────────────────────────────


@friend_request_matcher.handle()
async def handle_friend_request(bot: Bot, event: FriendRequestEvent) -> None:
    if event.request_type != "friend":
        return

    cfg = get_config()
    if not cfg["enabled"]:
        logger.info("[FriendManager] 插件已关闭，跳过好友请求 user_id=%s %s", event.user_id, describe_event(event))
        return

    user_id = int(event.user_id)
    comment = event.comment or ""

    # 黑名单检查
    if user_id in cfg["blocked_users"]:
        logger.info("[FriendManager] 拒绝黑名单用户 user_id=%s comment=%r %s", user_id, comment, describe_event(event))
        try:
            await event.reject(bot)
        except ActionFailed as e:
            logger.warning("[FriendManager] 拒绝好友请求失败 user_id=%s info=%s", user_id, getattr(e, "info", e))
        return

    # 白名单检查（非空时才生效）
    if cfg["allowed_users"] and user_id not in cfg["allowed_users"]:
        logger.info(
            "[FriendManager] 不在白名单中，跳过 user_id=%s comment=%r %s",
            user_id,
            comment,
            describe_event(event),
        )
        return

    # 验证消息关键词检查
    keyword = cfg["comment_keyword"]
    if keyword and keyword not in comment:
        logger.info(
            "[FriendManager] 验证消息不含关键词，跳过 user_id=%s comment=%r keyword=%r %s",
            user_id,
            comment,
            keyword,
            describe_event(event),
        )
        return

    if not cfg["auto_approve"]:
        logger.info("[FriendManager] auto_approve 已关闭，不自动通过 user_id=%s %s", user_id, describe_event(event))
        return

    try:
        await event.approve(bot)
        logger.info("[FriendManager] 已通过好友请求 user_id=%s comment=%r %s", user_id, comment, describe_event(event))
    except ActionFailed as e:
        logger.warning("[FriendManager] 通过好友请求失败 user_id=%s info=%s", user_id, getattr(e, "info", e))


# ── 好友添加通知 ────────────────────────────────────────────────────────────


def _format_event_time(raw: Any) -> str:
    try:
        timestamp = int(raw)
    except (TypeError, ValueError):
        return ""
    if timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp, tz=ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")


def _build_notify_text(user_id: int, nickname: str, raw_time: Any) -> str:
    return get_message(
        "friend_manager.superuser_notify",
        user_id=user_id,
        nickname=nickname,
        time=_format_event_time(raw_time),
    )


@register_push_source(
    "friend_add",
    description="新好友添加通知，event_data 提供 user_id/nickname/time。",
)
def friend_add_source(ctx: PushContext) -> list[str]:
    """好友添加事件推送源：读取 event_data 生成通知文本。"""
    data = ctx.event_data or {}
    if data.get("user_id") is None:
        return []
    nickname = str(data.get("nickname") or "").strip() or "未知"
    return [_build_notify_text(int(data["user_id"]), nickname, int(data.get("time") or 0))]


async def _send_fallback_notify(bot: Bot, user_id: int, nickname: str, raw_time: Any) -> None:
    superuser_id = str(load_main_config().get("bot", {}).get("superuser_id") or "").strip()
    if not superuser_id.isdigit():
        logger.warning("[FriendManager] superuser_id 未配置或非法，跳过新好友通知 user_id=%s", user_id)
        return
    try:
        await bot.send_private_msg(
            user_id=int(superuser_id),
            message=_build_notify_text(user_id, nickname, raw_time),
        )
        logger.info("[FriendManager] 已直接通知超级管理员新好友 user_id=%s", user_id)
    except ActionFailed as e:
        logger.warning("[FriendManager] 通知超级管理员失败 user_id=%s info=%s", user_id, getattr(e, "info", e))
    except Exception as e:
        logger.exception("[FriendManager] 通知超级管理员异常 user_id=%s: %s", user_id, e)


async def _notify_superuser(bot: Bot, user_id: int, raw_time: Any) -> None:
    """优先通过 push_framework 的 friend_add 消息源通知；未配置有效任务时直发超级管理员。"""
    nickname = "未知"
    try:
        info = await bot.get_stranger_info(user_id=user_id)
        nickname = str((info or {}).get("nickname") or "").strip() or "未知"
    except ActionFailed:
        logger.warning("[FriendManager] 获取新好友昵称失败 user_id=%s，通知显示「未知」", user_id)
    except Exception:
        logger.exception("[FriendManager] 获取新好友昵称异常 user_id=%s", user_id)

    event_data = {"user_id": user_id, "nickname": nickname, "time": raw_time}
    try:
        results = await run_jobs_by_source(bot, "friend_add", event_data=event_data)
    except Exception:
        logger.exception("[FriendManager] 推送好友添加通知异常 user_id=%s", user_id)
        results = []

    if not results or all(result.attempted == 0 for result in results):
        await _send_fallback_notify(bot, user_id, nickname, raw_time)


@friend_add_matcher.handle()
async def handle_friend_add(bot: Bot, event: FriendAddNoticeEvent) -> None:
    if event.notice_type != "friend_add":
        return

    cfg = get_config()
    if not cfg["enabled"]:
        return

    user_id = int(event.user_id)

    # 新好友通知（独立开关；未配置推送任务时直发超级管理员）
    if cfg["notify_superuser"]:
        await _notify_superuser(bot, user_id, getattr(event, "time", 0))

    if not cfg["welcome_enabled"]:
        return

    welcome_text = get_message("friend_manager.welcome")

    try:
        await bot.send_private_msg(user_id=user_id, message=welcome_text)
        logger.info("[FriendManager] 已发送欢迎消息 user_id=%s %s", user_id, describe_event(event))
    except ActionFailed as e:
        logger.warning("[FriendManager] 发送欢迎消息失败 user_id=%s info=%s", user_id, getattr(e, "info", e))
    except Exception as e:
        logger.exception("[FriendManager] 发送欢迎消息异常 user_id=%s: %s", user_id, e)
