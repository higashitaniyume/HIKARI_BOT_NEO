"""
网易云音乐解析插件入口。

NoneBot 加载此插件时自动注册：
1. 自动 URL 检测 handler → 注册到 message_pipeline
2. 检测 music.163.com 歌曲链接 → API 获取 MP3 → 下载 → 发送语音
"""

import asyncio
import logging

from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent

from core.access_control import is_event_allowed
from core.activity_tracker import ActivityScope
from core.bot_messages import get_message as msg
from core.error_notifier import notify_error_to_superuser, send_user_error
from core.message_pipeline import register_handler
from core.stats_tracker import increment as stats_increment

from .config import get_config
from .downloader import download_audio
from .parser import extract_song_ids, fetch_song_detail, fetch_song_url
from .sender import send_song

logger = logging.getLogger("HikariBot.NeteasePlugin")

# 触发首次加载并输出配置摘要
get_config()


async def _process_single_song(
    bot: Bot,
    event: MessageEvent,
    song_id: str,
    cfg: dict,
) -> None:
    """处理单个歌曲 ID 的完整流程：获取详情 → 获取 URL → 下载 → 发送。"""
    api_base = str(cfg.get("api_base_url", "http://127.0.0.1:3000"))
    api_timeout = int(cfg.get("api_timeout", 30))
    real_ip = str(cfg.get("real_ip", "")).strip()
    cache_dir = str(cfg.get("cache_dir", "/tmp/hikari_bot/netease"))
    max_file_mb = int(cfg.get("max_file_mb", 50))
    cache_ttl = int(cfg.get("cache_ttl_seconds", 600))

    # 1. 获取歌曲详情
    logger.debug("[Netease] 获取歌曲详情 → id=%s", song_id)
    song = await fetch_song_detail(song_id, api_base, api_timeout, real_ip)
    if not song or not song.name:
        await bot.send(event, Message(msg("netease.not_found")))
        return

    # 2. 获取音频 URL
    logger.debug("[Netease] 获取音频 URL → id=%s", song_id)
    url_result = await fetch_song_url(song_id, api_base, api_timeout, real_ip)
    if not url_result.url:
        await bot.send(event, Message(msg("netease.url_unavailable")))
        return

    # 3. 下载音频
    logger.debug("[Netease] 下载音频 → id=%s", song_id)
    audio_path = await download_audio(
        url_result.url,
        cache_dir=cache_dir,
        timeout=api_timeout,
        max_file_mb=max_file_mb,
        cache_ttl_seconds=cache_ttl,
    )

    # 4. 发送
    await send_song(bot, event, song, audio_path, cfg)


class AutoNeteaseHandler:
    """自动检测网易云音乐歌曲链接并解析的 Handler。"""

    name = "NeteaseParser"

    async def match(self, event: MessageEvent, text: str) -> bool:
        cfg = get_config()
        if not cfg.get("auto_parse", True):
            return False
        if not is_event_allowed(cfg, event):
            return False
        return bool(extract_song_ids(text))

    async def handle(self, bot: Bot, event: MessageEvent) -> None:
        cfg = get_config()
        if not is_event_allowed(cfg, event):
            return
        text = str(event.get_message())
        ids = extract_song_ids(text)
        if not ids:
            return

        max_links = max(1, int(cfg.get("max_links_per_message", 5)))
        ids_to_process = ids[:max_links]
        total_found = len(ids)

        logger.info(
            "[Netease] 自动解析触发 → user=%s, "
            "发现 %d 个链接, 处理 %d 个, ids=%s",
            event.get_user_id(),
            total_found,
            len(ids_to_process),
            ids_to_process,
        )

        for i, song_id in enumerate(ids_to_process):
            logger.debug(
                "[Netease] 处理第 %d/%d 个 → id=%s",
                i + 1,
                len(ids_to_process),
                song_id,
            )
            try:
                with ActivityScope(
                    "netease_parser",
                    "parsing",
                    "解析网易云音乐",
                    description=f"ID={song_id}",
                ):
                    await _process_single_song(bot, event, song_id, cfg)
                stats_increment(event, "netease_parsed", 1)
                await asyncio.sleep(1.0)
            except Exception as e:
                logger.exception("[Netease] 解析失败 → id=%s: %s", song_id, e)
                try:
                    await send_user_error(bot, event)
                    await notify_error_to_superuser(bot, event, e, "NeteaseParser")
                except Exception as notify_err:
                    logger.exception("发送错误通知失败: %s", notify_err)


# 注册到消息处理管道
register_handler(AutoNeteaseHandler())
logger.info("网易云音乐解析器已注册 → music.163.com")
