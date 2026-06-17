"""
Pixiv 解析插件入口。

NoneBot 加载此插件时自动注册：
1. 自动 URL 检测 handler → 注册到 message_pipeline
2. /pixiv 命令 → 手动触发解析（仅接受 URL）
"""

import asyncio
import logging

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
from nonebot.params import CommandArg

from core.message_pipeline import register_handler
from core.error_notifier import notify_error_to_superuser, send_user_error

from .config import load_config
from .parser import extract_pixiv_ids
from .sender import send_artwork

logger = logging.getLogger("HikariBot.PixivPlugin")

# 加载配置
config = load_config()


# =========================
# Auto URL Handler
# =========================

class AutoPixivHandler:
    """自动检测 Pixiv URL 并解析的 Handler。"""

    name = "PixivParser"

    async def match(self, event: MessageEvent, text: str) -> bool:
        if not config.get("auto_parse", True):
            return False
        return bool(extract_pixiv_ids(text))

    async def handle(self, bot: Bot, event: MessageEvent) -> None:
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
                await send_artwork(bot, event, illust_id, config)
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


# =========================
# /pixiv 命令
# =========================

pixiv_cmd = on_command(
    "pixiv",
    aliases={"Pixiv", "p站"},
    priority=5,
    block=True,
)


@pixiv_cmd.handle()
async def handle_pixiv_cmd(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """处理 /pixiv 命令，仅接受 Pixiv 作品 URL。"""
    raw = args.extract_plain_text().strip()
    user_id = event.get_user_id()
    logger.info(f"[Pixiv] 收到命令 → user={user_id}, input={raw[:100]}")

    if not raw:
        await pixiv_cmd.finish(
            "用法：\n"
            "/pixiv <Pixiv作品链接>\n\n"
            "示例：\n"
            "/pixiv https://www.pixiv.net/artworks/123456789\n"
            "/pixiv https://www.pixiv.net/i/123456789"
        )

    ids = extract_pixiv_ids(raw)
    if not ids:
        logger.info(f"[Pixiv] 未识别到 Pixiv 作品链接 → user={user_id}, input={raw[:50]}")
        await pixiv_cmd.finish(
            "未识别到 Pixiv 作品链接。\n"
            "请发送完整的 Pixiv URL，例如：\n"
            "https://www.pixiv.net/artworks/123456789"
        )

    # 只处理第一个链接
    illust_id = ids[0]
    try:
        await send_artwork(bot, event, illust_id, config)
    except Exception as e:
        logger.exception(f"[Pixiv] 命令解析失败 → pid={illust_id}: {e}")
        await pixiv_cmd.finish(f"Pixiv 解析失败：{e}")
