"""
Cobalt 社交媒体解析插件入口。

NoneBot 加载此插件时自动注册：
1. 自动 URL 检测 handler → 注册到 message_pipeline
2. Instagram / Facebook URL → cobalt API → 下载 → 发送
"""

import asyncio
import logging

from nonebot.adapters.onebot.v11 import Bot, MessageEvent

from core.message_pipeline import register_handler
from core.error_notifier import notify_error_to_superuser, send_user_error

from .config import get_config
from .parser import extract_social_urls, call_cobalt_api
from .sender import send_cobalt_result

logger = logging.getLogger("HikariBot.CobaltPlugin")

# 触发首次加载并输出配置摘要
get_config()


class AutoCobaltHandler:
    """自动检测 Instagram / Facebook URL 并解析的 Handler。"""

    name = "CobaltParser"

    async def match(self, event: MessageEvent, text: str) -> bool:
        cfg = get_config()
        if not cfg.get("auto_parse", True):
            return False
        return bool(extract_social_urls(text))

    async def handle(self, bot: Bot, event: MessageEvent) -> None:
        cfg = get_config()
        text = str(event.get_message())
        urls = extract_social_urls(text)
        if not urls:
            return

        # 最多处理 2 个链接，防止刷屏
        urls_to_process = urls[:2]
        total_found = len(urls)

        logger.info(
            f"[Cobalt] 自动解析触发 → user={event.get_user_id()}, "
            f"发现 {total_found} 个链接, 处理 {len(urls_to_process)} 个"
        )

        for i, url in enumerate(urls_to_process):
            logger.debug(f"[Cobalt] 处理第 {i+1}/{len(urls_to_process)} 个 → {url[:60]}...")
            try:
                result = await call_cobalt_api(url, cfg.get("cobalt_api", "http://192.168.31.2:54257/"), cfg.get("api_key", ""), cfg.get("api_timeout", 90))

                if result.status == "error":
                    await bot.send(event, f"解析失败：{result.error_code}\n链接：{url}")
                    continue

                await send_cobalt_result(bot, event, result, cfg)
                await asyncio.sleep(1.0)

            except Exception as e:
                logger.exception(f"[Cobalt] 解析失败 → {url[:60]}...: {e}")
                try:
                    await send_user_error(bot, event)
                    await notify_error_to_superuser(bot, event, e, "CobaltParser")
                except Exception as notify_err:
                    logger.exception(f"发送错误通知失败: {notify_err}")


# 注册到消息处理管道
register_handler(AutoCobaltHandler())
logger.info("Cobalt 社交媒体解析器已注册 → Instagram / Facebook")
