"""
表情包触发插件。

检测消息中的关键词，发送对应文件夹中的随机表情包。
- 纯关键词消息（如只发 "capoo"）→ 发送随机表情包
- 配置热重载：修改 sticker_trigger.json 立即生效
"""

import logging
import random
from pathlib import Path

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment

from .config import get_config

logger = logging.getLogger("HikariBot.StickerPlugin")

# 触发首次加载
get_config()

# 支持的图片/动图后缀
MEDIA_EXTS = {".gif", ".jpg", ".jpeg", ".png", ".webp", ".mp4"}


def _pick_random_file(folder: str) -> Path | None:
    """从文件夹中随机选取一个媒体文件。"""
    folder_path = Path(folder)
    if not folder_path.is_dir():
        return None

    files = [f for f in folder_path.iterdir() if f.is_file() and f.suffix.lower() in MEDIA_EXTS]
    if not files:
        return None

    return random.choice(files)


# =========================
# Matcher：中等优先级，不阻塞 pipeline
# =========================

sticker_matcher = on_message(priority=10, block=False)


@sticker_matcher.handle()
async def handle_sticker(bot: Bot, event: MessageEvent):
    """检测关键词并发送随机表情包。"""
    cfg = get_config()
    triggers: dict[str, str] = cfg.get("triggers", {})
    if not triggers:
        return

    text = event.get_plaintext().strip()
    if not text:
        return

    # 精确匹配关键词
    folder = triggers.get(text)
    if folder is None:
        return

    # 随机选取文件
    picked = _pick_random_file(folder)
    if picked is None:
        logger.warning(f"[Sticker] 关键词 '{text}' 匹配, 但文件夹 {folder} 无可用媒体文件")
        return

    logger.info(f"[Sticker] 关键词 '{text}' → {picked.name}")
    uri = picked.resolve().as_uri()
    await bot.send(event, Message(MessageSegment.image(uri)))
