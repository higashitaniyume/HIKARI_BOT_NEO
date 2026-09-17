"""
QQ 音乐音频发送模块。

负责构建歌曲信息文本，并通过 ``upload_group_file`` / ``upload_private_file``
（或语音消息）把音频送到 QQ 聊天。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from nonebot.adapters.onebot.v11 import (
    Bot,
    Event,
    GroupMessageEvent,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)

from core.bot_messages import get_message as msg

from .api import QQTrackDetail
from .downloader import QQAudioResult, file_as_uri

logger = logging.getLogger("HikariBot.QQMusicSender")


def _sanitize_filename(text: str) -> str:
    """清理文件名中的非法字符。"""
    return "".join(c for c in text if c.isprintable() and c not in r'<>:"/\|?*').strip()


def format_duration(seconds: int) -> str:
    """格式化时长为 mm:ss 或 hh:mm:ss。"""
    if seconds <= 0:
        return "未知"
    minutes, sec = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minute:02d}:{sec:02d}"
    return f"{minute}:{sec:02d}"


def format_size(size: int) -> str:
    """格式化文件大小为人类可读形式。"""
    mb = size / 1024 / 1024
    if mb >= 1024:
        return f"{mb / 1024:.2f}GB"
    if mb >= 1:
        return f"{mb:.1f}MB"
    return f"{size / 1024:.0f}KB"


def build_info_text(detail: QQTrackDetail, result: QQAudioResult) -> str:
    """构建歌曲信息文本。"""
    return msg(
        "qqmusic.info",
        name=detail.name or result.title or "未知歌曲",
        singer=detail.singer_text,
        album=detail.album_text,
        duration=format_duration(detail.interval or result.duration),
        quality=result.quality_label,
        size=format_size(result.filesize),
    )


def build_file_name(detail: QQTrackDetail, result: QQAudioResult) -> str:
    """构建群文件/私聊文件名：歌手 - 歌名.ext。"""
    singer = _sanitize_filename(detail.singer_text) if detail.singers else "未知歌手"
    name = _sanitize_filename(detail.name or result.title) or "未知歌曲"
    return f"{singer} - {name}{result.ext}"


async def send_qqmusic_audio(
    bot: Bot,
    event: Event,
    detail: QQTrackDetail,
    result: QQAudioResult,
    config: dict[str, Any],
) -> None:
    """发送 QQ 音乐音频到聊天。

    根据 ``config.send_strategy``：

    - ``"upload"``（默认）：用 ``upload_group_file`` / ``upload_private_file`` 发文件，
      保留原始音质与文件名，适合音乐。
    - ``"record"``：用 ``MessageSegment.record()`` 发语音消息。
    """
    if not result.path.is_file():
        raise FileNotFoundError(f"音频文件不存在: {result.path}")

    strategy = str(config.get("send_strategy", "upload"))

    if strategy == "record":
        await bot.send(event, Message(MessageSegment.record(file_as_uri(result.path))))
        logger.info("[QQMusic] 语音消息已发送 → %s", result.path.name)
        return

    file_name = build_file_name(detail, result)
    size_mb = result.filesize / 1024 / 1024
    logger.info(
        "[QQMusic] 上传文件 → %s (%.1fMB, name=%s)",
        result.path.name, size_mb, file_name,
    )

    if isinstance(event, GroupMessageEvent):
        await bot.call_api(
            "upload_group_file",
            group_id=event.group_id,
            file=str(result.path),
            name=file_name,
        )
        logger.info("[QQMusic] 群文件上传完成 → %s", file_name)
    elif isinstance(event, PrivateMessageEvent):
        await bot.call_api(
            "upload_private_file",
            user_id=event.user_id,
            file=str(result.path),
            name=file_name,
        )
        logger.info("[QQMusic] 私聊文件上传完成 → %s", file_name)
    else:
        # 未知事件类型，降级为语音消息
        logger.warning("[QQMusic] 未知事件类型，降级为语音发送 → %s", result.path.name)
        await bot.send(event, Message(MessageSegment.record(file_as_uri(result.path))))


def audio_path_of(result: QQAudioResult) -> Path:
    """便于测试/日志读取的音频路径。"""
    return result.path
