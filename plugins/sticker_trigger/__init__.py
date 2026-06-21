"""
表情包触发插件。

检测消息中的关键词，发送对应文件夹中的随机表情包。
- 纯关键词消息（如只发 "capoo"）→ 发送随机表情包
- 配置热重载：修改 sticker_trigger.json 立即生效
"""

import hashlib
import logging
import random
import shutil
from pathlib import Path

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment

from .config import get_config

logger = logging.getLogger("HikariBot.StickerPlugin")

# 触发首次加载
get_config()

# 支持的图片/动图后缀
MEDIA_EXTS = {".gif", ".jpg", ".jpeg", ".png", ".webp", ".mp4"}

# NapCat 共享目录（NapCat 容器必须挂载此目录）
SHARED_DIR = Path("/tmp/hikari_bot/stickers")


def _pick_random_file(folder: str) -> Path | None:
    """从文件夹中随机选取一个媒体文件。"""
    folder_path = Path(folder)
    if not folder_path.is_dir():
        return None

    files = [f for f in folder_path.iterdir() if f.is_file() and f.suffix.lower() in MEDIA_EXTS]
    if not files:
        return None

    return random.choice(files)


def _copy_to_shared(source: Path) -> Path:
    """将表情包复制到 NapCat 可读的共享目录，避免重复复制。"""
    SHARED_DIR.mkdir(parents=True, exist_ok=True)

    # 用文件名 + 内容哈希避免冲突和重复
    name_hash = hashlib.sha256(source.name.encode()).hexdigest()[:12]
    dest = SHARED_DIR / f"{name_hash}{source.suffix}"

    if not dest.exists():
        shutil.copy2(source, dest)
        logger.debug(f"[Sticker] 已复制到共享目录 → {dest}")

    return dest


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

    # "随机表情包" → 从所有贴纸包中随机选一张发送
    if text == "随机表情包":
        all_files: list[Path] = []
        seen_folders = set()
        for folder_path_str in set(triggers.values()):
            if folder_path_str in seen_folders:
                continue
            seen_folders.add(folder_path_str)
            folder_path = Path(folder_path_str)
            if folder_path.is_dir():
                all_files.extend(
                    f for f in folder_path.iterdir()
                    if f.is_file() and f.suffix.lower() in MEDIA_EXTS
                )
        if not all_files:
            await bot.send(event, Message("贴纸包都是空的，请先添加一些表情包。"))
            return
        picked = random.choice(all_files)
        logger.info(f"[Sticker] 随机表情包 → {picked.name}")
        shared_path = _copy_to_shared(picked)
        uri = shared_path.resolve().as_uri()
        await bot.send(event, Message(MessageSegment.image(uri)))
        return

    # "贴纸包" → 列出所有可用贴纸包（按文件夹聚合关键词）
    if text == "贴纸包":
        if not triggers:
            await bot.send(event, Message("当前没有可用的贴纸包。"))
            return
        groups: dict[str, list[str]] = {}
        for keyword, folder in triggers.items():
            folder_name = Path(folder).name
            groups.setdefault(folder_name, []).append(keyword)
        lines = ["当前贴纸包：", ""]
        for folder_name, keywords in groups.items():
            lines.append(f"· {folder_name}: {', '.join(keywords)}")
        await bot.send(event, Message("\n".join(lines)))
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

    # 复制到 NapCat 共享目录再发送
    shared_path = _copy_to_shared(picked)
    uri = shared_path.resolve().as_uri()
    await bot.send(event, Message(MessageSegment.image(uri)))
