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
import time
from pathlib import Path

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment

from .config import get_config
from core.stats_tracker import increment as stats_increment, format_stats

logger = logging.getLogger("HikariBot.StickerPlugin")

# 触发首次加载
get_config()

# 支持的图片/动图后缀
MEDIA_EXTS = {".gif", ".jpg", ".jpeg", ".png", ".webp", ".mp4"}

# NapCat 共享目录（NapCat 容器必须挂载此目录）
SHARED_DIR = Path("/tmp/hikari_bot/stickers")


def _cleanup_shared_dir():
    """删除超过 2 分钟的临时贴纸文件，避免堆积。"""
    if not SHARED_DIR.is_dir():
        return
    now = time.time()
    removed = 0
    for f in SHARED_DIR.iterdir():
        if f.is_file() and now - f.stat().st_mtime > 120:
            f.unlink(missing_ok=True)
            removed += 1
    if removed:
        logger.debug(f"[Sticker] 清理临时文件 {removed} 个")


def _copy_to_shared(source: Path) -> Path:
    """将表情包复制到 NapCat 可读的共享目录。"""
    SHARED_DIR.mkdir(parents=True, exist_ok=True)

    # 用完整路径哈希，避免不同贴纸包同名文件碰撞
    path_hash = hashlib.sha256(str(source.resolve()).encode()).hexdigest()[:16]
    dest = SHARED_DIR / f"{path_hash}{source.suffix}"

    if not dest.exists():
        shutil.copy2(source, dest)
        logger.debug(f"[Sticker] 已复制到共享目录 → {dest}")

    return dest


async def _make_collage(files: list[Path], folder_name: str) -> Path:
    """将所有图片的第一帧拼成尽可能正方形的网格图。

    使用线程池执行 PIL 操作，避免阻塞事件循环。
    """
    import asyncio
    import math
    from PIL import Image

    THUMB_SIZE = 200  # 每格缩略图尺寸

    def _do_collage() -> Path:
        images: list[Image.Image] = []
        for f in sorted(files):
            try:
                img = Image.open(f)
                # GIF/动画取第一帧
                if getattr(img, "is_animated", False):
                    img.seek(0)
                # 统一转 RGB
                if img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGBA")
                # 缩放到统一尺寸（保持比例，填白）
                img.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.Resampling.LANCZOS)
                bg = Image.new("RGBA", (THUMB_SIZE, THUMB_SIZE), (255, 255, 255, 0))
                ox = (THUMB_SIZE - img.width) // 2
                oy = (THUMB_SIZE - img.height) // 2
                bg.paste(img, (ox, oy), img if img.mode == "RGBA" else None)
                images.append(bg)
            except Exception as e:
                logger.warning(f"[Sticker] 拼图跳过 {f.name}: {e}")

        if not images:
            raise RuntimeError("没有可处理的图片")

        # 尽可能正方形
        n = len(images)
        cols = math.ceil(math.sqrt(n))
        rows = math.ceil(n / cols)

        canvas = Image.new("RGB", (cols * THUMB_SIZE, rows * THUMB_SIZE), (255, 255, 255))
        for i, img in enumerate(images):
            row = i // cols
            col = i % cols
            canvas.paste(img.convert("RGB"), (col * THUMB_SIZE, row * THUMB_SIZE))

        out_path = SHARED_DIR / f"collage_{folder_name}_{len(images)}.jpg"
        canvas.save(out_path, "JPEG", quality=85)
        return out_path

    return await asyncio.to_thread(_do_collage)


async def _send_forward(bot: Bot, event: MessageEvent, files: list[Path]):
    """合并转发多张表情包。"""
    from nonebot.adapters.onebot.v11 import GroupMessageEvent

    nodes: list[MessageSegment] = []
    for f in files:
        shared = _copy_to_shared(f)
        uri = shared.resolve().as_uri()
        nodes.append(MessageSegment.node_custom(
            user_id=int(bot.self_id),
            nickname="HikariBotNeo",
            content=Message(MessageSegment.image(uri)),
        ))

    if isinstance(event, GroupMessageEvent):
        await bot.send_group_forward_msg(group_id=event.group_id, messages=nodes)
    else:
        await bot.send_private_forward_msg(user_id=int(event.get_user_id()), messages=nodes)


# =========================
# Matcher：中等优先级，不阻塞 pipeline
# =========================

sticker_matcher = on_message(priority=10, block=False)


GIFS_ROOT = Path("BotData/Gifs")


def _build_lookup(triggers: dict) -> dict[str, str]:
    """从 {folder: [keywords]} 构建 {keyword: folder_name} 反向查找表。"""
    lookup: dict[str, str] = {}
    for folder_name, keywords in triggers.items():
        if isinstance(keywords, list):
            for kw in keywords:
                lookup[str(kw)] = folder_name
    return lookup


@sticker_matcher.handle()
async def handle_sticker(bot: Bot, event: MessageEvent):
    """检测关键词并发送随机表情包。"""
    cfg = get_config()
    triggers: dict = cfg.get("triggers", {})
    if not triggers:
        return

    text = event.get_plaintext().strip()
    if not text:
        return

    # "随机表情包" → 从所有贴纸包中随机选一张发送
    if text == "随机贴纸":
        all_files: list[Path] = []
        for folder_name in triggers:
            folder_path = GIFS_ROOT / folder_name
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
        stats_increment(event, "stickers_sent", 1)
        return

    # "拼图 capoo" → 将该贴纸包所有图片的第一帧拼成一张大图
    if text.startswith("拼图 "):
        keyword = text[3:].strip()
        lookup = _build_lookup(triggers)
        folder_name = lookup.get(keyword)
        if folder_name is None:
            return
        folder_path = GIFS_ROOT / folder_name
        if not folder_path.is_dir():
            return
        all_in_folder = [f for f in folder_path.iterdir() if f.is_file() and f.suffix.lower() in MEDIA_EXTS]
        if not all_in_folder:
            await bot.send(event, Message(f"贴纸包 {folder_name} 是空的。"))
            return

        await bot.send(event, Message(f"正在拼图 {folder_name}（{len(all_in_folder)} 张）..."))
        try:
            jpg_path = await _make_collage(all_in_folder, folder_name)
            uri = jpg_path.resolve().as_uri()
            await bot.send(event, Message(MessageSegment.image(uri)))
            stats_increment(event, "collage_made", 1)
        except Exception as e:
            logger.exception(f"[Sticker] 拼图失败: {e}")
            await bot.send(event, Message(f"拼图失败: {e}"))
        return

    # "统计" → 显示当前会话的统计信息
    if text == "统计":
        await bot.send(event, Message(format_stats(event)))
        return

    # "贴纸包" → 列出所有可用贴纸包
    if text == "贴纸包列表":
        lines = ["当前贴纸包：", ""]
        for folder_name, keywords in triggers.items():
            kw_list = keywords if isinstance(keywords, list) else [keywords]
            folder_path = GIFS_ROOT / folder_name
            count = 0
            if folder_path.is_dir():
                count = sum(1 for f in folder_path.iterdir() if f.is_file() and f.suffix.lower() in MEDIA_EXTS)
            lines.append(f"· {folder_name} ({count}张): {', '.join(kw_list)}")
        await bot.send(event, Message("\n".join(lines)))
        return

    # 解析关键词和可选数量："猫猫虫" 或 "猫猫虫 10"
    lookup = _build_lookup(triggers)
    keyword = text
    count = 1
    if " " in text:
        parts = text.rsplit(" ", 1)
        if parts[1].isdigit():
            keyword = parts[0]
            count = int(parts[1])

    folder_name = lookup.get(keyword)
    if folder_name is None:
        return

    # 从文件夹随机选取 count 张不重复的表情包
    folder_path = GIFS_ROOT / folder_name
    if not folder_path.is_dir():
        return

    all_in_folder = [f for f in folder_path.iterdir() if f.is_file() and f.suffix.lower() in MEDIA_EXTS]
    if not all_in_folder:
        logger.warning(f"[Sticker] 关键词 '{keyword}' 匹配, 但文件夹 {folder_name} 无可用媒体文件")
        return

    picked = random.sample(all_in_folder, min(count, len(all_in_folder)))

    logger.info(f"[Sticker] 关键词 '{keyword}' x{len(picked)} → {[p.name for p in picked]}")

    stats_increment(event, "stickers_sent", len(picked))

    if len(picked) <= 10:
        for p in picked:
            shared_path = _copy_to_shared(p)
            uri = shared_path.resolve().as_uri()
            await bot.send(event, Message(MessageSegment.image(uri)))
    else:
        try:
            await _send_forward(bot, event, picked)
        except Exception:
            for p in picked:
                shared_path = _copy_to_shared(p)
                await bot.send(event, Message(MessageSegment.image(shared_path.resolve().as_uri())))

    _cleanup_shared_dir()
