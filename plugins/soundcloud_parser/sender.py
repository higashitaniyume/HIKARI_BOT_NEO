"""
SoundCloud 音频发送模块。
"""

from __future__ import annotations

import logging
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

from .downloader import SoundCloudDownloadResult, file_as_uri

logger = logging.getLogger("HikariBot.SoundCloudSender")


def _sanitize_filename(text: str) -> str:
    """清理文件名中的非法字符。"""
    return "".join(c for c in text if c.isprintable() and c not in r'<>:"/\|?*').strip()


def _format_duration(seconds: int) -> str:
    """格式化时长为 mm:ss 或 hh:mm:ss。"""
    if seconds <= 0:
        return "未知"
    minutes, sec = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minute:02d}:{sec:02d}"
    return f"{minute}:{sec:02d}"


def _format_size(size: int) -> str:
    """格式化文件大小为人类可读形式。"""
    mb = size / 1024 / 1024
    if mb >= 1024:
        return f"{mb / 1024:.2f}GB"
    return f"{mb:.1f}MB"


def build_info_text(result: SoundCloudDownloadResult) -> str:
    """构建音频信息文本。"""
    return msg(
        "soundcloud.info",
        title=result.title,
        uploader=result.uploader,
        duration=_format_duration(result.duration),
        size=_format_size(result.filesize),
        url=result.webpage_url,
    )


async def send_soundcloud_track(
    bot: Bot,
    event: Event,
    result: SoundCloudDownloadResult,
    config: dict[str, Any],
) -> None:
    """发送 SoundCloud 音频到 QQ 聊天。

    根据 config.send_strategy:
      - "record"（默认）: 使用 MessageSegment.record() 发送语音消息
      - "upload": 使用 upload_group_file / upload_private_file 发送文件
    """
    strategy = str(config.get("send_strategy", "record"))

    if strategy == "upload":
        # 文件上传模式（类似网易云音乐）
        file_ext = result.path.suffix
        file_name = _sanitize_filename(f"{result.uploader} - {result.title}{file_ext}")
        if isinstance(event, GroupMessageEvent):
            await bot.call_api(
                "upload_group_file",
                group_id=event.group_id,
                file=str(result.path),
                name=file_name,
            )
            logger.info("[SoundCloud] 群文件上传完成 -> %s", file_name)
        elif isinstance(event, PrivateMessageEvent):
            await bot.call_api(
                "upload_private_file",
                user_id=event.user_id,
                file=str(result.path),
                name=file_name,
            )
            logger.info("[SoundCloud] 私聊文件上传完成 -> %s", file_name)
        else:
            # 未知事件类型，降级为语音消息
            logger.warning("[SoundCloud] 未知事件类型，降级为语音发送 -> %s", result.path.name)
            await bot.send(event, Message(MessageSegment.record(file_as_uri(result.path))))
    else:
        # 语音消息模式（默认）
        await bot.send(event, Message(MessageSegment.record(file_as_uri(result.path))))


async def download_and_send_soundcloud(
    bot: Bot,
    event: Event,
    url: str,
    config: dict[str, Any],
) -> None:
    """下载 SoundCloud 音频并发送到聊天。"""
    from .downloader import download_soundcloud_track

    result = await download_soundcloud_track(url, config)

    # 发送信息文本（可选）
    if bool(config.get("send_link_info", True)):
        await bot.send(event, Message(build_info_text(result)))

    # 发送音频
    await send_soundcloud_track(bot, event, result, config)
