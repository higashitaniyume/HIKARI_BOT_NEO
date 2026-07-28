"""
SoundCloud 自动解析插件入口。

NoneBot 加载此插件时自动注册 URL 检测 handler。
使用 yt-dlp 下载音频并发送到 QQ 聊天。
"""

from __future__ import annotations

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
from .downloader import SoundCloudDownloadError
from .parser import extract_soundcloud_urls
from .sender import download_and_send_soundcloud

logger = logging.getLogger("HikariBot.SoundCloudPlugin")

get_config()


class AutoSoundCloudHandler:
    """自动检测 SoundCloud URL 并下载音频的 Handler。"""

    name = "SoundCloudParser"

    async def match(self, event: MessageEvent, text: str) -> bool:
        cfg = get_config()
        if not cfg.get("enabled", True) or not cfg.get("auto_parse", True):
            return False
        if not is_event_allowed(cfg, event):
            return False
        return bool(extract_soundcloud_urls(text))

    async def handle(self, bot: Bot, event: MessageEvent) -> None:
        cfg = get_config()
        if not is_event_allowed(cfg, event):
            return
        text = str(event.get_message())
        urls = extract_soundcloud_urls(text)
        if not urls:
            return

        max_links = max(1, int(cfg.get("max_links_per_message", 3)))
        urls_to_process = urls[:max_links]

        logger.info(
            "[SoundCloud] 自动解析触发 -> user=%s, found=%d, process=%d",
            event.get_user_id(),
            len(urls),
            len(urls_to_process),
        )

        for index, url in enumerate(urls_to_process, start=1):
            logger.info("[SoundCloud] 处理链接 %d/%d -> %s", index, len(urls_to_process), url[:100])
            try:
                with ActivityScope("soundcloud_parser", "downloading", "下载 SoundCloud", description=url[:80]):
                    await download_and_send_soundcloud(bot, event, url, cfg)
                stats_increment(event, "soundcloud_downloaded", 1)
                await asyncio.sleep(1.0)
            except SoundCloudDownloadError as e:
                logger.warning("[SoundCloud] 下载失败 -> %s: %s", url[:100], e)
                await bot.send(event, Message(msg("soundcloud.failed", reason=str(e))))
            except Exception as e:
                logger.exception("[SoundCloud] 自动解析异常 -> %s: %s", url[:100], e)
                try:
                    await send_user_error(bot, event)
                    await notify_error_to_superuser(bot, event, e, "SoundCloudParser")
                except Exception as notify_err:
                    logger.exception("发送错误通知失败: %s", notify_err)


register_handler(AutoSoundCloudHandler())
logger.info("SoundCloud 解析器已注册 → soundcloud.com")
