"""
YouTube 视频发送模块。
"""

from __future__ import annotations

import logging
from typing import Any

from nonebot.adapters.onebot.v11 import Bot, Event, Message, MessageSegment

from core.bot_messages import get_message as msg

from .downloader import YouTubeDownloadResult, download_youtube_video, file_as_uri

logger = logging.getLogger("HikariBot.YouTubeSender")


def _format_duration(seconds: int) -> str:
    if seconds <= 0:
        return "未知"
    minutes, sec = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minute:02d}:{sec:02d}"
    return f"{minute}:{sec:02d}"


def _format_size(size: int) -> str:
    mb = size / 1024 / 1024
    if mb >= 1024:
        return f"{mb / 1024:.2f}GB"
    return f"{mb:.1f}MB"


def build_info_text(result: YouTubeDownloadResult) -> str:
    """构建视频来源信息文本。"""
    return msg(
        "youtube.info",
        title=result.title,
        uploader=result.uploader,
        duration=_format_duration(result.duration),
        size=_format_size(result.filesize),
        url=result.webpage_url,
    )


async def send_youtube_video(
    bot: Bot,
    event: Event,
    url: str,
    config: dict[str, Any],
) -> None:
    """下载并发送 YouTube 视频。"""
    await bot.send(event, Message(msg("youtube.start")))
    result = await download_youtube_video(url, config)
    await bot.send(event, Message(build_info_text(result)))
    await bot.send(event, Message(MessageSegment.video(file_as_uri(result.path))))
