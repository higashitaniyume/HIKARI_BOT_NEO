"""
Cobalt 社交媒体解析插件入口。

NoneBot 加载此插件时自动注册：
1. 自动 URL 检测 handler → 注册到 message_pipeline
2. Instagram / Facebook URL → cobalt API → 下载 → 发送
"""

import asyncio
import logging

from nonebot.adapters.onebot.v11 import Bot, MessageEvent

from core.access_control import is_event_allowed
from core.activity_tracker import ActivityScope
from core.message_pipeline import register_handler
from core.error_notifier import notify_error_to_superuser, send_user_error
from core.stats_tracker import increment as stats_increment

from .config import get_config
from .parser import CobaltResult, extract_social_urls, call_cobalt_api
from .sender import send_cobalt_result

logger = logging.getLogger("HikariBot.CobaltPlugin")

# 触发首次加载并输出配置摘要
get_config()


async def call_cobalt_api_with_retries(url: str, cfg: dict) -> CobaltResult:
    """调用 Cobalt API，按配置对临时失败做重试。"""
    retry_count = max(0, int(cfg.get("parse_retry_count", 2)))
    retry_delay = max(0.0, float(cfg.get("parse_retry_delay_seconds", 2.0)))
    attempts = retry_count + 1
    last_result: CobaltResult | None = None

    for attempt in range(1, attempts + 1):
        try:
            result = await call_cobalt_api(
                url,
                cfg.get("cobalt_api", "http://192.168.31.2:54257/"),
                cfg.get("api_key", ""),
                cfg.get("api_timeout", 90),
            )
        except Exception:
            if attempt >= attempts:
                raise
            logger.warning(
                "[Cobalt] API 请求异常，%.1fs 后重试 %d/%d → %s",
                retry_delay,
                attempt,
                retry_count,
                url[:80],
                exc_info=True,
            )
            if retry_delay > 0:
                await asyncio.sleep(retry_delay)
            continue

        if result.status != "error":
            if attempt > 1:
                logger.info("[Cobalt] API 重试成功 → attempt=%d/%d", attempt, attempts)
            return result

        last_result = result
        if attempt >= attempts:
            return result

        logger.warning(
            "[Cobalt] API 返回错误 code=%s，%.1fs 后重试 %d/%d → %s",
            result.error_code,
            retry_delay,
            attempt,
            retry_count,
            url[:80],
        )
        if retry_delay > 0:
            await asyncio.sleep(retry_delay)

    if last_result is not None:
        return last_result
    raise RuntimeError("Cobalt API 重试失败")


class AutoCobaltHandler:
    """自动检测 Instagram / Facebook URL 并解析的 Handler。"""

    name = "CobaltParser"

    async def match(self, event: MessageEvent, text: str) -> bool:
        cfg = get_config()
        if not cfg.get("auto_parse", True):
            return False
        if not is_event_allowed(cfg, event):
            return False
        return bool(extract_social_urls(text))

    async def handle(self, bot: Bot, event: MessageEvent) -> None:
        cfg = get_config()
        if not is_event_allowed(cfg, event):
            return
        text = str(event.get_message())
        urls = extract_social_urls(text)
        if not urls:
            return

        max_links = max(1, int(cfg.get("max_links_per_message", 20)))
        urls_to_process = urls[:max_links]
        total_found = len(urls)

        logger.info(
            f"[Cobalt] 自动解析触发 → user={event.get_user_id()}, "
            f"发现 {total_found} 个链接, 处理 {len(urls_to_process)} 个"
        )

        for i, url in enumerate(urls_to_process):
            logger.debug(f"[Cobalt] 处理第 {i+1}/{len(urls_to_process)} 个 → {url[:60]}...")
            try:
                with ActivityScope("cobalt_parser", "parsing", "解析媒体", description=url[:80]):
                    result = await call_cobalt_api_with_retries(url, cfg)

                if result.status == "error":
                    logger.warning(
                        "[Cobalt] API 无法解析 → code=%s, url=%s",
                        result.error_code,
                        url[:80],
                    )
                    await send_user_error(bot, event)
                    continue

                await send_cobalt_result(bot, event, result, cfg)
                stats_increment(event, "cobalt_parsed", 1)
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
