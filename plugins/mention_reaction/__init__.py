"""React to empty group mentions with a configured QQ emoji."""

from __future__ import annotations

import logging
import random
from typing import Any, Iterable

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent
from nonebot.adapters.onebot.v11.exception import ActionFailed

from core.command_router import is_command_handled, mark_event_handled
from core.lifecycle_logging import describe_event

from .config import get_config

logger = logging.getLogger("HikariBot.MentionReaction")

mention_reaction_matcher = on_message(priority=5, block=False)
logger.info("[MentionReaction] 空 @ 表情回应插件已加载")


@mention_reaction_matcher.handle()
async def handle_mention_reaction(bot: Bot, event: MessageEvent) -> None:
    if is_command_handled(event) or not isinstance(event, GroupMessageEvent):
        return

    cfg = get_config()
    self_id = event_self_id(event, bot)
    sender_id = _parse_int(event.get_user_id())
    if self_id is None or sender_id is None:
        logger.warning(
            "[MentionReaction] 缺少有效账号 ID self_id=%r bot_self_id=%r sender_id=%r %s",
            getattr(event, "self_id", None),
            getattr(bot, "self_id", None),
            event.get_user_id(),
            describe_event(event),
        )
        return

    if not should_react_to_empty_mention(
        is_group=True,
        is_tome=_looks_like_tome(event),
        sender_id=sender_id,
        self_id=self_id,
        group_id=int(event.group_id),
        message=event.get_message(),
        config=cfg,
    ):
        if _looks_like_tome(event):
            logger.info(
                "[MentionReaction] 跳过 @ 消息 self_id=%s segments=%s %s",
                self_id,
                summarize_segments(event.get_message()),
                describe_event(event, event.get_plaintext()),
            )
        return

    emoji_id = choose_emoji_id(cfg)
    if not emoji_id:
        return

    mark_event_handled(event)
    message_id = parse_message_id(getattr(event, "message_id", None))
    if message_id is None:
        logger.warning("[MentionReaction] 缺少有效 message_id，无法添加表情回应 %s", describe_event(event))
        return

    try:
        await send_msg_emoji_like(bot, message_id=message_id, emoji_id=emoji_id)
        logger.info(
            "[MentionReaction] 已添加表情回应 emoji_id=%s %s",
            emoji_id,
            describe_event(event),
        )
    except ActionFailed as e:
        logger.warning(
            "[MentionReaction] 表情回应失败 emoji_id=%s message_id=%s info=%s",
            emoji_id,
            message_id,
            getattr(e, "info", e),
        )
    except Exception as e:
        logger.exception("[MentionReaction] 表情回应异常 emoji_id=%s message_id=%s: %s", emoji_id, message_id, e)


def should_react_to_empty_mention(
    *,
    is_group: bool,
    is_tome: bool = False,
    sender_id: int,
    self_id: int,
    group_id: int | None,
    message: Iterable[Any],
    config: dict[str, Any],
) -> bool:
    if not bool(config.get("enabled", True)):
        return False
    if not bool(config.get("group_enabled", True)):
        return False
    if not is_group:
        return False
    if sender_id == self_id:
        return False
    if str(sender_id) in _as_str_set(config.get("ignored_users")):
        return False

    allowed_groups = _as_str_set(config.get("allowed_groups"))
    if allowed_groups and str(group_id or "") not in allowed_groups:
        return False

    return _contains_only_self_at_and_blank_text(message, self_id) or (
        bool(is_tome) and _contains_only_blank_text(message)
    )


def choose_emoji_id(config: dict[str, Any]) -> str:
    emoji_ids = [str(item).strip() for item in config.get("emoji_ids", []) if str(item).strip()]
    if not emoji_ids:
        return ""
    if bool(config.get("random", False)):
        return random.choice(emoji_ids)
    return emoji_ids[0]


async def send_msg_emoji_like(bot: Any, *, message_id: int, emoji_id: str) -> Any:
    return await bot.call_api("set_msg_emoji_like", message_id=message_id, emoji_id=str(emoji_id))


def event_self_id(event: Any, bot: Any) -> int | None:
    return _parse_int(getattr(event, "self_id", None)) or _parse_int(getattr(bot, "self_id", None))


def parse_message_id(value: Any) -> int | None:
    return _parse_int(value)


def summarize_segments(message: Iterable[Any]) -> str:
    parts: list[str] = []
    for segment in message:
        segment_type = str(getattr(segment, "type", "") or "")
        data = getattr(segment, "data", {}) or {}
        if segment_type == "text":
            text = str(data.get("text") or "")
            parts.append(f"text:{text!r}")
        elif segment_type == "at":
            parts.append(f"at:{data.get('qq')!r}")
        else:
            keys = ",".join(sorted(str(key) for key in data.keys())[:4])
            parts.append(f"{segment_type}<{keys}>")
    return "[" + ", ".join(parts) + "]"


def _contains_only_blank_text(message: Iterable[Any]) -> bool:
    for segment in message:
        segment_type = getattr(segment, "type", "")
        data = getattr(segment, "data", {}) or {}
        if segment_type == "text" and not str(data.get("text") or "").strip():
            continue
        return False
    return True


def _contains_only_self_at_and_blank_text(message: Iterable[Any], self_id: int) -> bool:
    has_self_at = False
    for segment in message:
        segment_type = getattr(segment, "type", "")
        data = getattr(segment, "data", {}) or {}
        if segment_type == "at":
            if _parse_int(data.get("qq")) != self_id:
                return False
            has_self_at = True
            continue
        if segment_type == "text" and not str(data.get("text") or "").strip():
            continue
        return False
    return has_self_at


def _parse_int(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text.isdigit():
        return None
    return int(text)


def _as_str_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}


def _looks_like_tome(event: MessageEvent) -> bool:
    try:
        return bool(event.is_tome())
    except Exception:
        return False
