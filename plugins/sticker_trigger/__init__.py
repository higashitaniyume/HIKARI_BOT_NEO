"""
表情包触发插件。

检测消息中的关键词，发送对应贴纸包中的随机表情包。
- 纯关键词消息（如只发 "capoo"）→ 发送随机表情包
- 贴纸库索引：由 sticker_library.json 管理贴纸包、关键词和文件关系
"""

import asyncio
import hashlib
import logging
import random
import re
import shutil
import time
from pathlib import Path
from typing import Awaitable, Callable, TypeVar

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed

from core.command_router import CommandContext, command, is_command_handled
from core.error_notifier import notify_error_to_superuser
from core.stats_tracker import increment as stats_increment, format_stats
from plugins import sticker_library

logger = logging.getLogger("HikariBot.StickerPlugin")

# 贴纸包最终只发送 GIF；其他素材应先经过 media_transcoder 转换
MEDIA_EXTS = sticker_library.MEDIA_EXTS
PACK_LIST_PAGE_SIZE = 5
SEND_RETRY_ATTEMPTS = 2
SEND_RETRY_DELAY_SECONDS = 2.0

# NapCat 共享目录（NapCat 容器必须挂载此目录）
SHARED_DIR = Path("/tmp/hikari_bot/stickers")
T = TypeVar("T")


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


def _safe_output_label(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", value).strip(" ._")
    return value[:48] or "stickers"


def _is_send_timeout(error: ActionFailed) -> bool:
    text = f"{getattr(error, 'message', '')}\n{getattr(error, 'wording', '')}"
    return getattr(error, "retcode", None) == 1200 or "Timeout" in text


async def _send_with_retry(
    action: Callable[[], Awaitable[T]],
    label: str,
    *,
    attempts: int = SEND_RETRY_ATTEMPTS,
) -> T:
    last_error: ActionFailed | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await action()
        except ActionFailed as e:
            last_error = e
            if not _is_send_timeout(e) or attempt >= attempts:
                raise
            logger.warning(
                "[Sticker] %s 发送超时，%.1fs 后重试 %d/%d: %s",
                label,
                SEND_RETRY_DELAY_SECONDS,
                attempt,
                attempts - 1,
                e,
            )
            await asyncio.sleep(SEND_RETRY_DELAY_SECONDS)

    assert last_error is not None
    raise last_error


async def _send_image(bot: Bot, event: MessageEvent, path: Path, label: str) -> None:
    shared_path = _copy_to_shared(path)
    uri = shared_path.resolve().as_uri()
    await _send_with_retry(
        lambda: bot.send(event, Message(MessageSegment.image(uri))),
        label,
    )


async def _try_send_text(bot: Bot, event: MessageEvent, text: str, label: str) -> None:
    try:
        await _send_with_retry(lambda: bot.send(event, Message(text)), label)
    except Exception as e:
        logger.warning("[Sticker] %s 文本提示发送失败: %s", label, e)


def _log_send_failure(label: str, error: Exception) -> None:
    if isinstance(error, ActionFailed) and _is_send_timeout(error):
        logger.warning("[Sticker] %s 发送超时: %s", label, error)
    else:
        logger.exception("[Sticker] %s 发送失败: %s", label, error)


async def _notify_sticker_error(bot: Bot, event: MessageEvent, error: Exception, feature: str) -> None:
    try:
        await notify_error_to_superuser(bot, event, error, feature)
    except Exception as notify_error:
        logger.exception("[Sticker] 发送管理员错误通知失败: %s", notify_error)


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

        label = _safe_output_label(folder_name)
        out_path = SHARED_DIR / f"collage_{label}_{len(images)}.jpg"
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


async def _send_text_forward(bot: Bot, event: MessageEvent, texts: list[str]) -> None:
    """合并转发多段文本。"""
    from nonebot.adapters.onebot.v11 import GroupMessageEvent

    nodes: list[MessageSegment] = [
        MessageSegment.node_custom(
            user_id=int(bot.self_id),
            nickname="HikariBotNeo",
            content=Message(text),
        )
        for text in texts
    ]

    if isinstance(event, GroupMessageEvent):
        await bot.send_group_forward_msg(group_id=event.group_id, messages=nodes)
    else:
        await bot.send_private_forward_msg(user_id=int(event.get_user_id()), messages=nodes)


# =========================
# Matcher：中等优先级，不阻塞 pipeline
# =========================

sticker_matcher = on_message(priority=10, block=False)


def _format_folder_label(folder_names: list[str]) -> str:
    if len(folder_names) == 1:
        return folder_names[0]
    return " + ".join(folder_names)


def _sticker_library_stats_lines(state: dict) -> list[str]:
    return [
        "贴纸库统计：",
        f"· 唯一贴纸：{state.get('total_stickers', 0)} 张",
        f"· 贴纸包：{len(state.get('packs') or [])} 个",
        f"· 关键词：{len(state.get('keywords') or [])} 个",
        "· 同一关键词命中多个包时会合并去重",
    ]


def _format_keyword_preview(keywords: list[str], limit: int = 6) -> str:
    if not keywords:
        return "暂无关键词"
    preview = keywords[:limit]
    suffix = f" 等 {len(keywords)} 个" if len(keywords) > limit else ""
    return f"{', '.join(preview)}{suffix}"


def _format_pack_list_page(state: dict, page: int) -> str:
    packs = state.get("packs") or []
    total_pages = max(1, (len(packs) + PACK_LIST_PAGE_SIZE - 1) // PACK_LIST_PAGE_SIZE)
    page = min(max(page, 1), total_pages)
    start = (page - 1) * PACK_LIST_PAGE_SIZE
    current_packs = packs[start:start + PACK_LIST_PAGE_SIZE]

    lines = [
        *_sticker_library_stats_lines(state),
        "",
        f"贴纸包列表 {page}/{total_pages}：",
    ]

    if not current_packs:
        lines.append("暂无贴纸包。")
    else:
        for pack in current_packs:
            keywords = _format_keyword_preview(pack.get("keywords") or [])
            lines.append(f"· {pack['name']} ({pack['count']}张): {keywords}")

    if page < total_pages:
        lines.append("")
        lines.append(f"发送「贴纸包列表 {page + 1}」查看下一页")
    lines.append("发送「贴纸包统计」只看总数")
    return "\n".join(lines)


async def _send_pack_list(bot: Bot, event: MessageEvent, arg: str) -> None:
    state = sticker_library.get_state()
    packs = state.get("packs") or []
    total_pages = max(1, (len(packs) + PACK_LIST_PAGE_SIZE - 1) // PACK_LIST_PAGE_SIZE)

    if arg in {"全部", "all", "ALL"}:
        pages = [_format_pack_list_page(state, page) for page in range(1, total_pages + 1)]
        try:
            await _send_text_forward(bot, event, pages)
        except Exception as e:
            logger.exception("[Sticker] 合并转发贴纸包列表失败: %s", e)
            await bot.send(event, Message("完整列表发送失败，请使用「贴纸包列表 <页码>」分页查看。"))
        return

    page = 1
    if arg:
        if not arg.isdigit():
            await bot.send(event, Message("用法：贴纸包列表、贴纸包列表 <页码>、贴纸包列表 全部"))
            return
        page = int(arg)

    if page < 1 or page > total_pages:
        await bot.send(event, Message(f"页码超出范围，目前共有 {total_pages} 页。"))
        return

    await bot.send(event, Message(_format_pack_list_page(state, page)))


def _is_reserved_command_text(text: str) -> bool:
    return (
        text in {"随机贴纸", "统计", "贴纸包统计", "贴纸包列表"}
        or text.startswith("拼图 ")
        or text.startswith("贴纸包列表 ")
    )


@command("随机贴纸", description="从所有贴纸包随机发送一张贴纸")
async def cmd_random_sticker(ctx: CommandContext) -> None:
    all_files = sticker_library.get_all_files()
    if not all_files:
        await ctx.send(Message("贴纸包都是空的，请先添加一些表情包。"))
        return
    picked = random.choice(all_files)
    logger.info(f"[Sticker] 随机表情包 → {picked.name}")
    await _send_image(ctx.bot, ctx.event, picked, "随机贴纸")
    stats_increment(ctx.event, "stickers_sent", 1)


@command("拼图", description="生成关键词对应贴纸包的预览拼图", usage="拼图 <关键词>")
async def cmd_sticker_collage(ctx: CommandContext) -> None:
    keyword = ctx.args.strip()
    if not keyword:
        await ctx.send(Message("用法：拼图 <关键词>"))
        return

    folder_names, all_in_folders = sticker_library.get_files_for_keyword(keyword)
    if not folder_names:
        return

    folder_label = _format_folder_label(folder_names)
    if not all_in_folders:
        await ctx.send(Message(f"贴纸包 {folder_label} 是空的。"))
        return

    await _try_send_text(ctx.bot, ctx.event, f"正在拼图 {folder_label}（{len(all_in_folders)} 张）...", "拼图进度")
    try:
        jpg_path = await _make_collage(all_in_folders, f"{keyword}_{len(folder_names)}packs")
    except Exception as e:
        logger.exception("[Sticker] 拼图生成失败: %s", e)
        await _notify_sticker_error(ctx.bot, ctx.event, e, "StickerCollage")
        await _try_send_text(ctx.bot, ctx.event, "拼图失败，请稍后再试。", "拼图失败提示")
        return

    try:
        await _send_image(ctx.bot, ctx.event, jpg_path, "拼图")
        stats_increment(ctx.event, "collage_made", 1)
    except Exception as e:
        _log_send_failure("拼图", e)
        await _notify_sticker_error(ctx.bot, ctx.event, e, "StickerCollageSend")
        await _try_send_text(
            ctx.bot,
            ctx.event,
            "拼图已经生成，但发送图片超时了。可以稍后重试，或检查 NapCat/QQ 是否卡住。",
            "拼图失败提示",
        )


@command("统计", description="查看当前会话统计")
async def cmd_session_stats(ctx: CommandContext) -> None:
    await ctx.send(Message(format_stats(ctx.event)))


@command("贴纸包统计", description="查看贴纸库摘要")
async def cmd_sticker_pack_stats(ctx: CommandContext) -> None:
    await ctx.send(Message("\n".join(_sticker_library_stats_lines(sticker_library.get_state()))))


@command("贴纸包列表", aliases=("sticker packs",), description="分页查看贴纸包", usage="贴纸包列表 [页码|全部]")
async def cmd_sticker_pack_list(ctx: CommandContext) -> None:
    await _send_pack_list(ctx.bot, ctx.event, ctx.args.strip())


@sticker_matcher.handle()
async def handle_sticker(bot: Bot, event: MessageEvent):
    """检测关键词并发送随机表情包。"""
    if is_command_handled(event):
        return

    text = event.get_plaintext().strip()
    if not text:
        return

    if _is_reserved_command_text(text):
        return

    # 解析关键词和可选数量："猫猫虫" 或 "猫猫虫 10"
    keyword = text
    count = 1
    if " " in text:
        parts = text.rsplit(" ", 1)
        if parts[1].isdigit():
            keyword = parts[0]
            count = int(parts[1])

    folder_names, all_in_folders = sticker_library.get_files_for_keyword(keyword)
    if not folder_names:
        return

    # 从关键词对应的所有文件夹里随机选取 count 张不重复的表情包
    if not all_in_folders:
        logger.warning(f"[Sticker] 关键词 '{keyword}' 匹配, 但贴纸包 {_format_folder_label(folder_names)} 无可用媒体文件")
        return

    picked = random.sample(all_in_folders, min(count, len(all_in_folders)))

    logger.info(f"[Sticker] 关键词 '{keyword}' x{len(picked)} → {[p.name for p in picked]}")

    if len(picked) <= 10:
        sent = 0
        for p in picked:
            try:
                await _send_image(bot, event, p, f"贴纸 {p.name}")
                sent += 1
            except Exception as e:
                _log_send_failure(f"贴纸 {p.name}", e)
                await _notify_sticker_error(bot, event, e, "StickerSend")
        if sent:
            stats_increment(event, "stickers_sent", sent)
    else:
        try:
            await _send_with_retry(lambda: _send_forward(bot, event, picked), "贴纸合并转发")
            stats_increment(event, "stickers_sent", len(picked))
        except Exception as e:
            _log_send_failure("贴纸合并转发", e)
            await _notify_sticker_error(bot, event, e, "StickerForwardSend")
            logger.info("[Sticker] 合并转发失败，降级为逐张发送")
            sent = 0
            for p in picked:
                try:
                    await _send_image(bot, event, p, f"贴纸 {p.name}")
                    sent += 1
                except Exception as send_error:
                    _log_send_failure(f"贴纸 {p.name}", send_error)
                    await _notify_sticker_error(bot, event, send_error, "StickerSend")
            if sent:
                stats_increment(event, "stickers_sent", sent)

    _cleanup_shared_dir()
