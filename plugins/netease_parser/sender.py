"""
网易云音乐解析发送模块。

负责：
1. 构建歌曲信息文本
2. 发送信息文本后发送音频
"""

import logging
from pathlib import Path
from typing import Any

from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent, Message, MessageSegment

from core.bot_messages import get_message as msg

from .downloader import file_as_uri
from .parser import NeteaseSongInfo

logger = logging.getLogger("HikariBot.NeteaseSender")


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
    发送歌曲信息和音频。

    1. 先发送歌曲信息文本
    2. 再发送音频作为语音消息
    """
    send_link_info = bool(config.get("send_link_info", True))

    # 发送信息文本
    if send_link_info and (song.name or song.artist):
        info_text = build_info_text(song)
        await bot.send(event, Message(info_text))

    # 发送音频
    uri = file_as_uri(audio_path)
    logger.info("[Netease] 发送音频 → %s", audio_path.name)
    await bot.send(event, Message(MessageSegment.record(uri)))
