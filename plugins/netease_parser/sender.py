"""
网易云音乐解析发送模块。

负责：
1. 构建歌曲信息文本
2. 通过 upload_private_file / upload_group_file 发送音频文件
"""

import logging
from pathlib import Path
from typing import Any

from nonebot.adapters.onebot.v11 import (
    Bot,
    Event,
    GroupMessageEvent,
    Message,
    PrivateMessageEvent,
)

from core.bot_messages import get_message as msg

from .parser import NeteaseSongInfo

logger = logging.getLogger("HikariBot.NeteaseSender")


def _sanitize_filename(text: str) -> str:
    """清理文件名中的非法字符。"""
    return "".join(c for c in text if c.isprintable() and c not in r'<>:"/\|?*').strip()


def _build_filename(song: NeteaseSongInfo, ext: str) -> str:
    """构建文件名：歌手 - 歌名.ext。"""
    artist_part = _sanitize_filename(song.artist) if song.artist else "未知歌手"
    name_part = _sanitize_filename(song.name) if song.name else "未知歌曲"
    return f"{artist_part} - {name_part}{ext}"


def build_info_text(song: NeteaseSongInfo) -> str:
    """构建歌曲信息文本。"""
    return msg(
        "netease.info",
        name=song.name,
        artist=song.artist,
        album=song.album,
    )


async def send_song(
    bot: Bot,
    event: Event,
    song: NeteaseSongInfo,
    audio_path: Path,
    config: dict[str, Any],
) -> None:
    """
    发送歌曲信息和音频文件。

    1. 先发送歌曲信息文本
    2. 通过 upload_group_file / upload_private_file 发送音频文件
    """
    send_link_info = bool(config.get("send_link_info", True))
    file_ext = audio_path.suffix
    file_name = _build_filename(song, file_ext)

    # 发送信息文本
    if send_link_info and (song.name or song.artist):
        info_text = build_info_text(song)
        logger.info(
            "[Netease] 发送信息文本 → 「%s — %s」(%s)",
            song.name, song.artist, song.album,
        )
        await bot.send(event, Message(info_text))

    # 发送文件
    file_size_mb = audio_path.stat().st_size / 1024 / 1024 if audio_path.exists() else 0
    logger.info(
        "[Netease] 上传文件 → %s (%.1fMB, name=%s)",
        audio_path.name, file_size_mb, file_name,
    )

    if isinstance(event, GroupMessageEvent):
        await bot.call_api(
            "upload_group_file",
            group_id=event.group_id,
            file=str(audio_path),
            name=file_name,
        )
        logger.info("[Netease] 群文件上传完成 → %s", file_name)
    elif isinstance(event, PrivateMessageEvent):
        await bot.call_api(
            "upload_private_file",
            user_id=event.user_id,
            file=str(audio_path),
            name=file_name,
        )
        logger.info("[Netease] 私聊文件上传完成 → %s", file_name)
    else:
        # 未知事件类型，降级为语音消息发送
        from nonebot.adapters.onebot.v11 import MessageSegment

        uri = audio_path.resolve().as_uri()
        logger.warning("[Netease] 未知事件类型，降级为语音发送 → %s", audio_path.name)
        await bot.send(event, Message(MessageSegment.record(uri)))
