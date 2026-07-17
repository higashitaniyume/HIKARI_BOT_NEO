from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import shutil
import time
from pathlib import Path
from typing import Awaitable, Callable, TypeVar

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed

from core.command_router import is_command_handled, mark_event_handled
from core.stats_tracker import increment as stats_increment
from plugins import voice_library

logger = logging.getLogger("HikariBot.VoiceTrigger")

SHARED_DIR = Path("/tmp/hikari_bot/voices")
SEND_RETRY_ATTEMPTS = 2
SEND_RETRY_DELAY_SECONDS = 2.0
T = TypeVar("T")


def _cleanup_shared_dir() -> None:
    if not SHARED_DIR.is_dir():
        return
    now = time.time()
    removed = 0
    for path in SHARED_DIR.iterdir():
        if path.is_file() and now - path.stat().st_mtime > 300:
            path.unlink(missing_ok=True)
            removed += 1
    if removed:
        logger.debug("[Voice] 清理临时语音文件 %d 个", removed)


def _copy_to_shared(source: Path) -> Path:
    SHARED_DIR.mkdir(parents=True, exist_ok=True)
    path_hash = hashlib.sha256(str(source.resolve()).encode()).hexdigest()[:16]
    dest = SHARED_DIR / f"{path_hash}{source.suffix}"
    if not dest.exists() or dest.stat().st_size <= 0:
        shutil.copy2(source, dest)
        logger.debug("[Voice] 已复制到共享目录: %s", dest)
    return dest


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
            logger.warning(
                "[Voice] %s 发送超时，%.1fs 后重试 %d/%d: %s",
                label,
                SEND_RETRY_DELAY_SECONDS,
                attempt,
                attempts - 1,
                e,
            )
            await asyncio.sleep(SEND_RETRY_DELAY_SECONDS)

    assert last_error is not None
    raise last_error


async def _send_voice(bot: Bot, event: MessageEvent, path: Path, label: str) -> None:
    shared_path = _copy_to_shared(path)
    uri = shared_path.resolve().as_uri()
    await _send_with_retry(
        lambda: bot.send(event, Message(MessageSegment.record(uri))),
        label,
    )


voice_matcher = on_message(priority=10, block=False)


@voice_matcher.handle()
async def handle_voice(bot: Bot, event: MessageEvent) -> None:
    if is_command_handled(event):
        return

    text = event.get_plaintext().strip()
    if not text:
        return

    voices = voice_library.get_voices_for_keyword(text)
    if not voices:
        return

    picked = random.choice(voices)
    logger.info("[Voice] 关键词 %r -> %s", text, picked.name)
    stats_increment(event, "voice_triggers", 1)
    try:
        await _send_voice(bot, event, picked, f"语音 {picked.name}")
        mark_event_handled(event)
    except Exception as e:
        logger.exception("[Voice] 语音发送失败: %s", e)
    finally:
        _cleanup_shared_dir()
