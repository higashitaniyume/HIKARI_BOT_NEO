"""
贴纸触发插件发送模块。

处理各种发送方式：单张发送、合并转发、重试逻辑。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable, TypeVar

from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed

from core.bot_identity import get_bot_name
from core.error_notifier import notify_error_to_superuser

from .utils import _copy_to_shared

logger = logging.getLogger("HikariBot.StickerPlugin")

SEND_RETRY_ATTEMPTS = 3
SEND_RETRY_DELAY_BASE = 2.0
STICKER_FORWARD_CHUNK_SIZE = 80
STICKER_FORWARD_CHUNK_DELAY_SECONDS = 1.0
T = TypeVar("T")


def _is_send_timeout(error: ActionFailed) -> bool:
    text = f"{getattr(error, 'message', '')}\n{getattr(error, 'wording', '')}"
    return getattr(error, "retcode", None) == 1200 or "Timeout" in text


async def _send_with_retry(
    action: Callable[[], Awaitable[T]],
    label: str,
    *,
    attempts: int = SEND_RETRY_ATTEMPTS,
) -> T:
    last_error: ActionFailed | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await action()
        except ActionFailed as e:
            last_error = e
            if not _is_send_timeout(e) or attempt >= attempts:
                raise
            delay = SEND_RETRY_DELAY_BASE * (2 ** (attempt - 1))
            logger.warning(
                "[Sticker] %s 发送超时，%.1fs 后重试 %d/%d: %s",
                label,
                delay,
                attempt,
                attempts - 1,
                e,
            )
            await asyncio.sleep(delay)

    assert last_error is not None
    raise last_error


async def _send_image(bot: Bot, event: MessageEvent, path: Path, label: str) -> None:
    shared_path = _copy_to_shared(path)
    uri = shared_path.resolve().as_uri()
    await _send_with_retry(
        lambda: bot.send(event, Message(MessageSegment.image(uri))),
        label,
    )


async def _try_send_text(bot: Bot, event: MessageEvent, text: str, label: str) -> None:
    try:
        await _send_with_retry(lambda: bot.send(event, Message(text)), label)
    except Exception as e:
        logger.warning("[Sticker] %s 文本提示发送失败: %s", label, e)


def _log_send_failure(label: str, error: Exception) -> None:
    if isinstance(error, ActionFailed) and _is_send_timeout(error):
        logger.warning("[Sticker] %s 发送超时: %s", label, error)
    else:
        logger.exception("[Sticker] %s 发送失败: %s", label, error)


async def _notify_sticker_error(bot: Bot, event: MessageEvent, error: Exception, feature: str) -> None:
    try:
        await notify_error_to_superuser(bot, event, error, feature)
    except Exception as notify_error:
        logger.exception("[Sticker] 发送管理员错误通知失败: %s", notify_error)


async def _send_forward(bot: Bot, event: MessageEvent, files: list[Path]):
    """合并转发多张表情包。"""
    from nonebot.adapters.onebot.v11 import GroupMessageEvent

    nodes: list[MessageSegment] = []
    bot_nickname = get_bot_name()
    for f in files:
        shared = _copy_to_shared(f)
        uri = shared.resolve().as_uri()
        nodes.append(MessageSegment.node_custom(
            user_id=int(bot.self_id),
            nickname=bot_nickname,
            content=Message(MessageSegment.image(uri)),
        ))

    if isinstance(event, GroupMessageEvent):
        await bot.send_group_forward_msg(group_id=event.group_id, messages=nodes)
    else:
        await bot.send_private_forward_msg(user_id=int(event.get_user_id()), messages=nodes)


def _chunk_files(files: list[Path], chunk_size: int) -> list[list[Path]]:
    safe_size = max(1, int(chunk_size))
    return [files[i:i + safe_size] for i in range(0, len(files), safe_size)]


async def _send_many_stickers(bot: Bot, event: MessageEvent, picked: list[Path]) -> int:
    if len(picked) <= 10:
        sent = 0
        for p in picked:
            try:
                await _send_image(bot, event, p, f"贴纸 {p.name}")
                sent += 1
            except Exception as e:
                _log_send_failure(f"贴纸 {p.name}", e)
                await _notify_sticker_error(bot, event, e, "StickerSend")
        return sent

    sent = 0
    chunks = _chunk_files(picked, STICKER_FORWARD_CHUNK_SIZE)
    for index, chunk in enumerate(chunks, start=1):
        label = f"贴纸合并转发 {index}/{len(chunks)}"
        try:
            await _send_with_retry(lambda chunk=chunk: _send_forward(bot, event, chunk), label)
            sent += len(chunk)
        except Exception as e:
            _log_send_failure(label, e)
            await _notify_sticker_error(bot, event, e, "StickerForwardSend")
            logger.info("[Sticker] %s 失败，降级为逐张发送", label)
            for p in chunk:
                try:
                    await _send_image(bot, event, p, f"贴纸 {p.name}")
                    sent += 1
                except Exception as send_error:
                    _log_send_failure(f"贴纸 {p.name}", send_error)
                    await _notify_sticker_error(bot, event, send_error, "StickerSend")

        if index < len(chunks):
            await asyncio.sleep(STICKER_FORWARD_CHUNK_DELAY_SECONDS)

    return sent


async def _send_text_forward(bot: Bot, event: MessageEvent, texts: list[str]) -> None:
    """合并转发多段文本。"""
    from nonebot.adapters.onebot.v11 import GroupMessageEvent

    bot_nickname = get_bot_name()
    nodes: list[MessageSegment] = [
        MessageSegment.node_custom(
            user_id=int(bot.self_id),
            nickname=bot_nickname,
            content=Message(text),
        )
        for text in texts
    ]

    if isinstance(event, GroupMessageEvent):
        await bot.send_group_forward_msg(group_id=event.group_id, messages=nodes)
    else:
        await bot.send_private_forward_msg(user_id=int(event.get_user_id()), messages=nodes)
