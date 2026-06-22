"""
Pixiv 解析插件入口。

NoneBot 加载此插件时自动注册：
1. 自动 URL 检测 handler → 注册到 message_pipeline
"""

import asyncio
import logging

from nonebot.adapters.onebot.v11 import Bot, MessageEvent

from core.message_pipeline import register_handler
from core.error_notifier import notify_error_to_superuser, send_user_error
from core.stats_tracker import increment as stats_increment

from .config import get_config
from .parser import extract_pixiv_ids
from .sender import send_artwork

logger = logging.getLogger("HikariBot.PixivPlugin")

# 触发首次加载并输出配置摘要
get_config()


# =========================
# Auto URL Handler
# =========================

class AutoPixivHandler:
    """自动检测 Pixiv URL 并解析的 Handler。"""

    name = "PixivParser"

    async def match(self, event: MessageEvent, text: str) -> bool:
        cfg = get_config()
        if not cfg.get("auto_parse", True):
            return False
        return bool(extract_pixiv_ids(text))

    async def handle(self, bot: Bot, event: MessageEvent) -> None:
        cfg = get_config()
        text = str(event.get_message())
        ids = extract_pixiv_ids(text)
        if not ids:
            return

        # 一条消息最多处理 2 个 Pixiv 链接，防止刷屏
        ids_to_process = ids[:2]
        total_found = len(ids)

        logger.info(
            f"[Pixiv] 自动解析触发 → user={event.get_user_id()}, "
            f"发现 {total_found} 个链接, 处理 {len(ids_to_process)} 个, ids={ids_to_process}"
        )

        for i, illust_id in enumerate(ids_to_process):
            logger.debug(f"[Pixiv] 自动解析第 {i+1}/{len(ids_to_process)} 个 → pid={illust_id}")
            try:
                await send_artwork(bot, event, illust_id, cfg)
                stats_increment(event, "pixiv_parsed", 1)
                await asyncio.sleep(1.0)
            except Exception as e:
                logger.exception(f"[Pixiv] 自动解析失败 → pid={illust_id}: {e}")
                try:
                    await send_user_error(bot, event)
                    await notify_error_to_superuser(bot, event, e, "PixivParser")
                except Exception as notify_err:
                    logger.exception(f"发送错误通知失败: {notify_err}")


# 注册到消息处理管道
register_handler(AutoPixivHandler())
