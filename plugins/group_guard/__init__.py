"""群风控：AI 审查群成员消息里的极度政治敏感内容并撤回，另提供引用「撤回」命令。

审查在后台任务里跑，不阻塞消息管线（priority=6, block=False，命中才动作）。
群开关走 permissions 黑白名单，默认还要求群号显式出现在白名单里，避免误开导
致 AI 费用失控——费用由机器人主人承担。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent
from nonebot.adapters.onebot.v11.exception import ActionFailed

from core.access_control import is_event_allowed, normalize_access_rules
from core.bot_messages import get_message
from core.command_router import CommandContext, command, is_command_handled, is_superuser_event
from core.error_notifier import notify_superuser_message
from core.lifecycle_logging import describe_event

from plugins.aiagent.review import ReviewResult, request_verdict

from .config import get_config
from .recall import delete_message, is_group_admin

logger = logging.getLogger("HikariBot.GroupGuard")

guard_matcher = on_message(priority=6, block=False)

_semaphore: asyncio.Semaphore | None = None
_semaphore_size = 0
_pending: set[asyncio.Task[None]] = set()

logger.info("[GroupGuard] 群风控插件已加载")


# ── 群消息审查 ────────────────────────────────────────────────────────────


@guard_matcher.handle()
async def handle_group_review(bot: Bot, event: GroupMessageEvent) -> None:
    cfg = get_config()
    review_cfg = cfg["review"]
    if not cfg["enabled"] or not review_cfg["enabled"]:
        return
    if review_cfg["skip_handled"] and is_command_handled(event):
        return
    if str(event.user_id) == str(event.self_id):
        return
    if review_cfg["skip_superuser"] and is_superuser_event(event):
        return
    if not _group_under_guard(cfg, event):
        return

    text = event.get_plaintext().strip()
    if len(text) < review_cfg["min_chars"]:
        return
    message_id = _parse_int(getattr(event, "message_id", None))
    if message_id is None:
        return

    task = asyncio.create_task(_review_and_act(bot, event, cfg, text, message_id))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


def _group_under_guard(cfg: dict[str, Any], event: GroupMessageEvent) -> bool:
    if not is_event_allowed(cfg, event):
        return False
    if not cfg["review"]["require_group_whitelist"]:
        return True
    rules = normalize_access_rules(cfg.get("permissions", {}))
    return str(event.group_id) in rules["whitelist"]["group"]


async def _review_and_act(
    bot: Bot,
    event: GroupMessageEvent,
    cfg: dict[str, Any],
    text: str,
    message_id: int,
) -> None:
    review_cfg = cfg["review"]
    try:
        async with _get_semaphore(review_cfg["max_concurrent"]):
            result = await asyncio.wait_for(
                request_verdict(text, review_cfg, label="GroupGuard"),
                timeout=review_cfg["timeout_seconds"] + 5,
            )
    except asyncio.TimeoutError:
        logger.warning("[GroupGuard] 审查超时 message_id=%s %s", message_id, describe_event(event))
        return
    except Exception as e:
        logger.warning("[GroupGuard] 审查失败 message_id=%s: %s", message_id, e)
        return

    if result is None or not result.risk:
        return

    logger.warning(
        "[GroupGuard] 命中敏感内容 group=%s user=%s message_id=%s reason=%r",
        event.group_id,
        event.user_id,
        message_id,
        result.reason,
    )
    if not cfg["action"]["recall"]:
        return

    recalled = await delete_message(bot, message_id, context=f"guard group={event.group_id}")
    await _after_recall(bot, event, cfg, result, text, recalled)


async def _after_recall(
    bot: Bot,
    event: GroupMessageEvent,
    cfg: dict[str, Any],
    result: ReviewResult,
    text: str,
    recalled: bool,
) -> None:
    if recalled and cfg["action"]["notify_group"]:
        try:
            await bot.send_group_msg(
                group_id=int(event.group_id),
                message=get_message("group_guard.risk_group_notice"),
            )
        except ActionFailed as e:
            logger.warning("[GroupGuard] 群内提示发送失败 group=%s info=%s", event.group_id, getattr(e, "info", e))
        except Exception as e:
            logger.exception("[GroupGuard] 群内提示发送异常 group=%s: %s", event.group_id, e)

    if not cfg["action"]["notify_superuser"]:
        return
    await notify_superuser_message(
        bot,
        get_message(
            "group_guard.risk_superuser_notify",
            group_id=event.group_id,
            user_id=event.user_id,
            result="已撤回" if recalled else "撤回失败",
            reason=result.reason or "未提供",
            text=text[:300],
        ),
    )


# ── 撤回命令 ──────────────────────────────────────────────────────────────


@command(
    "撤回",
    description="引用一条消息回复「撤回」让机器人撤回它",
    usage="撤回",
    detail_key="group_guard.recall_help",
    category="群管理",
)
async def handle_recall_command(ctx: CommandContext) -> None:
    cfg = get_config()
    recall_cfg = cfg["recall_command"]
    if not cfg["enabled"] or not recall_cfg["enabled"]:
        return

    event = ctx.event
    reply = getattr(event, "reply", None)
    target_id = _parse_int(getattr(reply, "message_id", None)) if reply is not None else None
    if target_id is None:
        await _reply_failure(ctx, recall_cfg, "group_guard.recall_need_reply")
        return

    self_id = _parse_int(getattr(event, "self_id", None)) or _parse_int(getattr(ctx.bot, "self_id", None))
    reply_sender = _parse_int(getattr(getattr(reply, "sender", None), "user_id", None))

    # 场景 1：引用机器人自己的消息 —— 谁都可以让它撤回自己那条。
    if self_id is not None and reply_sender == self_id:
        if not recall_cfg["allow_self_recall"]:
            return
        if not await delete_message(ctx.bot, target_id, context="recall self"):
            await _reply_failure(ctx, recall_cfg, "group_guard.recall_failed")
        return

    # 场景 2：引用别人的消息，正文必须只有「撤回」两个字且不含任何 @。
    if not isinstance(event, GroupMessageEvent):
        await _reply_failure(ctx, recall_cfg, "group_guard.recall_only_own")
        return
    if not recall_cfg["allow_other_recall"] or not _is_bare_recall(event, ctx.args):
        return
    if recall_cfg["other_requires_admin"] and not await _may_recall_others(ctx.bot, event):
        await _reply_failure(ctx, recall_cfg, "group_guard.recall_permission_denied")
        return
    if self_id is None or not await is_group_admin(ctx.bot, event.group_id, self_id):
        await _reply_failure(ctx, recall_cfg, "group_guard.recall_bot_not_admin")
        return
    if not await delete_message(ctx.bot, target_id, context=f"recall other group={event.group_id}"):
        await _reply_failure(ctx, recall_cfg, "group_guard.recall_failed")


async def _may_recall_others(bot: Bot, event: GroupMessageEvent) -> bool:
    return is_superuser_event(event) or await is_group_admin(bot, event.group_id, event.user_id)


def _is_bare_recall(event: MessageEvent, args: str) -> bool:
    """引用段和自动 @ 已被适配器剥掉，所以剩下的只能是纯文本，且不带参数。"""
    if args.strip():
        return False
    return all(str(getattr(segment, "type", "") or "") == "text" for segment in event.get_message())


async def _reply_failure(ctx: CommandContext, recall_cfg: dict[str, Any], key: str) -> None:
    if not recall_cfg["reply_on_failure"]:
        return
    try:
        await ctx.send(get_message(key))
    except Exception as e:
        logger.warning("[GroupGuard] 撤回提示发送失败 key=%s: %s", key, e)


def _get_semaphore(size: int) -> asyncio.Semaphore:
    global _semaphore, _semaphore_size
    if _semaphore is None or _semaphore_size != size:
        _semaphore = asyncio.Semaphore(size)
        _semaphore_size = size
    return _semaphore


def _parse_int(value: Any) -> int | None:
    text = str(value if value is not None else "").strip()
    sign = 1
    if text.startswith("-"):
        sign = -1
        text = text[1:]
    if not text.isdigit():
        return None
    return sign * int(text)
