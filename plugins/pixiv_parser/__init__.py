"""
Pixiv 解析插件入口。

NoneBot 加载此插件时自动注册：
1. 自动 URL 检测 handler → 注册到 message_pipeline
"""

import logging

from nonebot.adapters.onebot.v11 import Bot, MessageEvent

from core.access_control import is_event_allowed
from core.message_pipeline import register_handler

from .config import get_config
from .parser import extract_pixiv_ids
from .queues import enqueue_artworks

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
        if not is_event_allowed(cfg, event):
            return False
        return bool(extract_pixiv_ids(text))

    async def handle(self, bot: Bot, event: MessageEvent) -> None:
        cfg = get_config()
        if not is_event_allowed(cfg, event):
            return
        text = str(event.get_message())
        ids = extract_pixiv_ids(text)
        if not ids:
            return

        max_links = max(1, int(cfg.get("max_links_per_message", 20)))
        ids_to_process = ids[:max_links]
        total_found = len(ids)

        logger.info(
            f"[Pixiv] 自动解析触发 → user={event.get_user_id()}, "
            f"发现 {total_found} 个链接, 处理 {len(ids_to_process)} 个, ids={ids_to_process}"
        )

        # 入队后台处理：消息链不再被多图串行下载阻塞（高并发下不阻塞用户）
        await enqueue_artworks(bot, event, ids_to_process)


# 注册到消息处理管道
register_handler(AutoPixivHandler())
