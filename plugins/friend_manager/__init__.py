"""Auto-accept friend requests and send a welcome message on friend add."""

from __future__ import annotations

import logging
from typing import Any

from nonebot import on_notice, on_request
from nonebot.adapters.onebot.v11 import Bot, FriendAddNoticeEvent, FriendRequestEvent
from nonebot.adapters.onebot.v11.exception import ActionFailed

from core.bot_messages import get_message
from core.lifecycle_logging import describe_event

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


@friend_add_matcher.handle()
async def handle_friend_add(bot: Bot, event: FriendAddNoticeEvent) -> None:
    if event.notice_type != "friend_add":
        return

    cfg = get_config()
    if not cfg["enabled"] or not cfg["welcome_enabled"]:
        return

    user_id = int(event.user_id)
    welcome_text = get_message("friend_manager.welcome")

    try:
        await bot.send_private_msg(user_id=user_id, message=welcome_text)
        logger.info("[FriendManager] 已发送欢迎消息 user_id=%s %s", user_id, describe_event(event))
    except ActionFailed as e:
        logger.warning("[FriendManager] 发送欢迎消息失败 user_id=%s info=%s", user_id, getattr(e, "info", e))
    except Exception as e:
        logger.exception("[FriendManager] 发送欢迎消息异常 user_id=%s: %s", user_id, e)
