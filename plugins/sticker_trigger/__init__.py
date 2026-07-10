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

from core.activity_tracker import ActivityScope
from core.bot_identity import get_bot_name
from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command, is_command_handled, mark_event_handled
from core.error_notifier import notify_error_to_superuser
from core.rendering import draw_text, load_font, text_size
from core.stats_tracker import increment as stats_increment, format_stats
from plugins import sticker_library

logger = logging.getLogger("HikariBot.StickerPlugin")

# 贴纸包最终只发送 GIF；其他素材应先经过 media_transcoder 转换
MEDIA_EXTS = sticker_library.MEDIA_EXTS
PACK_LIST_PAGE_SIZE = 5
SEND_RETRY_ATTEMPTS = 2
SEND_RETRY_DELAY_SECONDS = 2.0
STICKER_FORWARD_CHUNK_SIZE = 80
STICKER_FORWARD_CHUNK_DELAY_SECONDS = 1.0
PACK_PREVIEW_LIMIT = 6
PACK_PREVIEW_IMAGE_WIDTH = 1200

# NapCat 共享目录（NapCat 容器必须挂载此目录）
SHARED_DIR = Path("/tmp/hikari_bot/stickers")
T = TypeVar("T")

_PACK_SUBCOMMAND_ALIASES = {
    "help": "help",
    "帮助": "help",
    "菜单": "help",
    "random": "random",
    "随机": "random",
    "随机贴纸": "random",
    "贴纸": "random",
    "collage": "collage",
    "拼图": "collage",
    "stats": "stats",
    "stat": "stats",
    "统计": "stats",
    "列表": "list",
    "list": "list",
    "packs": "list",
    "预览": "preview",
    "preview": "preview",
}


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


def _text_width(draw, text: str, font) -> int:
    return text_size(draw, text, font)[0]


def _line_height(draw, font) -> int:
    return max(1, text_size(draw, "Ag国", font)[1])


def _wrap_text(draw, text: str, font, max_width: int, max_lines: int) -> list[str]:
    text = str(text or "").strip()
    if not text:
        return [""]

    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if current and _text_width(draw, candidate, font) > max_width:
            lines.append(current)
            current = char
            if len(lines) >= max_lines:
                break
        else:
            current = candidate

    if len(lines) < max_lines and current:
        lines.append(current)

    if len(lines) > max_lines:
        lines = lines[:max_lines]

    if lines and _text_width(draw, lines[-1], font) > max_width:
        while lines[-1] and _text_width(draw, lines[-1] + "...", font) > max_width:
            lines[-1] = lines[-1][:-1]
        lines[-1] = lines[-1] + "..."
    elif current and len("".join(lines)) < len(text):
        while lines[-1] and _text_width(draw, lines[-1] + "...", font) > max_width:
            lines[-1] = lines[-1][:-1]
        lines[-1] = lines[-1] + "..."

    return lines or [""]


def _load_preview_frame(path: Path, size: int):
    from PIL import Image

    with Image.open(path) as img:
        if getattr(img, "is_animated", False):
            img.seek(0)
        frame = img.convert("RGBA")
        frame.thumbnail((size, size), Image.Resampling.LANCZOS)
        tile = Image.new("RGBA", (size, size), (255, 255, 255, 0))
        x = (size - frame.width) // 2
        y = (size - frame.height) // 2
        tile.alpha_composite(frame, (x, y))
        return tile


async def _make_pack_preview_image() -> Path:
    import asyncio

    def _do_render() -> Path:
        from PIL import Image, ImageDraw

        state = sticker_library.get_state()
        packs = state.get("packs") or []
        if not packs:
            raise RuntimeError("暂无贴纸包。")

        width = PACK_PREVIEW_IMAGE_WIDTH
        margin = 36
        card_gap = 18
        card_padding = 22
        thumb_size = 132
        thumb_gap = 12
        title_font = load_font(34, bold=True)
        subtitle_font = load_font(22)
        name_font = load_font(28, bold=True)
        meta_font = load_font(18)
        keyword_font = load_font(20)
        scratch = Image.new("RGB", (width, 400), (255, 255, 255))
        draw = ImageDraw.Draw(scratch)

        preview_area_width = thumb_size * 3 + thumb_gap * 2
        text_width = width - margin * 2 - card_padding * 2 - preview_area_width - 28
        rows: list[dict] = []
        for pack in packs:
            keywords = pack.get("keywords") or []
            keyword_text = msg(
                "sticker.preview_keyword",
                keywords="、".join(str(item) for item in keywords) if keywords else msg("sticker.no_keywords"),
            )
            title_lines = _wrap_text(draw, str(pack.get("name") or ""), name_font, text_width, 2)
            keyword_lines = _wrap_text(draw, keyword_text, keyword_font, text_width, 3)
            text_height = (
                len(title_lines) * (_line_height(draw, name_font) + 4)
                + 10
                + _line_height(draw, meta_font)
                + 12
                + len(keyword_lines) * (_line_height(draw, keyword_font) + 5)
            )
            preview_ids = [str(item) for item in pack.get("previews") or []][:PACK_PREVIEW_LIMIT]
            preview_paths = [
                path
                for sticker_id in preview_ids
                if (path := sticker_library.get_sticker_path(sticker_id)) is not None
            ]
            preview_rows = 2 if len(preview_paths) > 3 else 1
            preview_height = preview_rows * thumb_size + max(0, preview_rows - 1) * thumb_gap
            card_height = max(text_height, preview_height) + card_padding * 2
            rows.append({
                "pack": pack,
                "title_lines": title_lines,
                "keyword_lines": keyword_lines,
                "preview_paths": preview_paths,
                "height": card_height,
            })

        header_height = 118
        total_height = margin + header_height + sum(row["height"] for row in rows) + card_gap * (len(rows) - 1) + margin
        image = Image.new("RGB", (width, total_height), (246, 248, 245))
        draw = ImageDraw.Draw(image)

        y = margin
        draw_text(draw, (margin, y), msg("sticker.preview_title"), fill=(26, 33, 28), font=title_font)
        y += 48
        summary = msg(
            "sticker.preview_summary",
            pack_count=len(packs),
            sticker_count=state.get("total_stickers", 0),
            keyword_count=len(state.get("keywords") or []),
        )
        draw_text(draw, (margin, y), summary, fill=(92, 104, 96), font=subtitle_font)
        y = margin + header_height

        for row in rows:
            pack = row["pack"]
            card_x = margin
            card_y = y
            card_w = width - margin * 2
            card_h = row["height"]
            draw.rounded_rectangle(
                (card_x, card_y, card_x + card_w, card_y + card_h),
                radius=18,
                fill=(255, 255, 255),
                outline=(220, 228, 220),
                width=2,
            )

            text_x = card_x + card_padding
            text_y = card_y + card_padding
            for line in row["title_lines"]:
                draw_text(draw, (text_x, text_y), line, fill=(24, 32, 27), font=name_font)
                text_y += _line_height(draw, name_font) + 4
            text_y += 8
            draw_text(
                draw,
                (text_x, text_y),
                msg("sticker.preview_pack_count", count=pack.get("count", 0)),
                fill=(92, 104, 96),
                font=meta_font,
            )
            text_y += _line_height(draw, meta_font) + 12
            for line in row["keyword_lines"]:
                draw_text(draw, (text_x, text_y), line, fill=(54, 68, 58), font=keyword_font)
                text_y += _line_height(draw, keyword_font) + 5

            preview_x = card_x + card_w - card_padding - preview_area_width
            preview_y = card_y + (card_h - (thumb_size * 2 + thumb_gap)) // 2
            preview_y = max(card_y + card_padding, preview_y)
            for index, path in enumerate(row["preview_paths"]):
                col = index % 3
                line = index // 3
                tile_x = preview_x + col * (thumb_size + thumb_gap)
                tile_y = preview_y + line * (thumb_size + thumb_gap)
                draw.rounded_rectangle(
                    (tile_x, tile_y, tile_x + thumb_size, tile_y + thumb_size),
                    radius=14,
                    fill=(246, 248, 245),
                    outline=(224, 231, 225),
                )
                try:
                    frame = _load_preview_frame(path, thumb_size - 16)
                    image.paste(frame.convert("RGB"), (tile_x + 8, tile_y + 8), frame)
                except Exception as e:
                    logger.warning("[Sticker] 贴纸包预览图加载失败: %s -> %s", path, e)

            if not row["preview_paths"]:
                empty_text = msg("sticker.preview_empty")
                tx = preview_x + (preview_area_width - _text_width(draw, empty_text, keyword_font)) // 2
                ty = card_y + card_h // 2 - _line_height(draw, keyword_font) // 2
                draw_text(draw, (tx, ty), empty_text, fill=(139, 149, 140), font=keyword_font)

            y += card_h + card_gap

        SHARED_DIR.mkdir(parents=True, exist_ok=True)
        out_path = SHARED_DIR / f"pack_preview_{int(time.time())}.png"
        image.save(out_path, "PNG", optimize=True)
        return out_path

    return await asyncio.to_thread(_do_render)


async def _send_forward(bot: Bot, event: MessageEvent, files: list[Path]):
    """合并转发多张表情包。"""
    from nonebot.adapters.onebot.v11 import GroupMessageEvent

    nodes: list[MessageSegment] = []
    bot_nickname = get_bot_name()
    for f in files:
        shared = _copy_to_shared(f)
        uri = shared.resolve().as_uri()
        nodes.append(MessageSegment.node_custom(
            user_id=int(bot.self_id),
            nickname=bot_nickname,
            content=Message(MessageSegment.image(uri)),
        ))

    if isinstance(event, GroupMessageEvent):
        await bot.send_group_forward_msg(group_id=event.group_id, messages=nodes)
    else:
        await bot.send_private_forward_msg(user_id=int(event.get_user_id()), messages=nodes)


def _chunk_files(files: list[Path], chunk_size: int) -> list[list[Path]]:
    safe_size = max(1, int(chunk_size))
    return [files[i:i + safe_size] for i in range(0, len(files), safe_size)]


async def _send_many_stickers(bot: Bot, event: MessageEvent, picked: list[Path]) -> int:
    if len(picked) <= 10:
        sent = 0
        for p in picked:
            try:
                await _send_image(bot, event, p, f"贴纸 {p.name}")
                sent += 1
            except Exception as e:
                _log_send_failure(f"贴纸 {p.name}", e)
                await _notify_sticker_error(bot, event, e, "StickerSend")
        return sent

    sent = 0
    chunks = _chunk_files(picked, STICKER_FORWARD_CHUNK_SIZE)
    for index, chunk in enumerate(chunks, start=1):
        label = f"贴纸合并转发 {index}/{len(chunks)}"
        try:
            await _send_with_retry(lambda chunk=chunk: _send_forward(bot, event, chunk), label)
            sent += len(chunk)
        except Exception as e:
            _log_send_failure(label, e)
            await _notify_sticker_error(bot, event, e, "StickerForwardSend")
            logger.info("[Sticker] %s 失败，降级为逐张发送", label)
            for p in chunk:
                try:
                    await _send_image(bot, event, p, f"贴纸 {p.name}")
                    sent += 1
                except Exception as send_error:
                    _log_send_failure(f"贴纸 {p.name}", send_error)
                    await _notify_sticker_error(bot, event, send_error, "StickerSend")

        if index < len(chunks):
            await asyncio.sleep(STICKER_FORWARD_CHUNK_DELAY_SECONDS)

    return sent


async def _send_text_forward(bot: Bot, event: MessageEvent, texts: list[str]) -> None:
    """合并转发多段文本。"""
    from nonebot.adapters.onebot.v11 import GroupMessageEvent

    bot_nickname = get_bot_name()
    nodes: list[MessageSegment] = [
        MessageSegment.node_custom(
            user_id=int(bot.self_id),
            nickname=bot_nickname,
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
    return msg(
        "sticker.library_stats",
        total_stickers=state.get("total_stickers", 0),
        pack_count=len(state.get("packs") or []),
        keyword_count=len(state.get("keywords") or []),
    ).splitlines()


def _format_keyword_preview(keywords: list[str], limit: int = 6) -> str:
    if not keywords:
        return msg("sticker.no_keywords")
    preview = keywords[:limit]
    suffix = msg("sticker.keyword_more", count=len(keywords)) if len(keywords) > limit else ""
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
        msg("sticker.pack_list_header", page=page, total_pages=total_pages),
    ]

    if not current_packs:
        lines.append(msg("sticker.no_packs"))
    else:
        for pack in current_packs:
            keywords = _format_keyword_preview(pack.get("keywords") or [])
            lines.append(msg("sticker.pack_list_row", name=pack["name"], count=pack["count"], keywords=keywords))

    if page < total_pages:
        lines.append("")
        lines.append(msg("sticker.pack_list_next_page", page=page + 1))
    lines.append(msg("sticker.pack_list_stats_hint"))
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
            await bot.send(event, Message(msg("sticker.pack_list_forward_failed")))
        return

    page = 1
    if arg:
        if not arg.isdigit():
            await bot.send(event, Message(msg("sticker.pack_list_usage")))
            return
        page = int(arg)

    if page < 1 or page > total_pages:
        await bot.send(event, Message(msg("sticker.pack_list_page_out_of_range", total_pages=total_pages)))
        return

    await bot.send(event, Message(_format_pack_list_page(state, page)))


def _is_reserved_command_text(text: str) -> bool:
    return text == "统计" or text == "贴纸包" or text.startswith("贴纸包 ")


async def cmd_random_sticker(ctx: CommandContext) -> None:
    all_files = sticker_library.get_all_files()
    if not all_files:
        await ctx.send(Message(msg("sticker.empty_library")))
        return
    picked = random.choice(all_files)
    logger.info(f"[Sticker] 随机表情包 → {picked.name}")
    await _send_image(ctx.bot, ctx.event, picked, "随机贴纸")
    stats_increment(ctx.event, "stickers_sent", 1)


async def cmd_sticker_collage(ctx: CommandContext) -> None:
    keyword = ctx.args.strip()
    if not keyword:
        await ctx.send(Message(msg("sticker.collage_usage")))
        return

    folder_names, all_in_folders = sticker_library.get_files_for_keyword(keyword)
    if not folder_names:
        return

    folder_label = _format_folder_label(folder_names)
    if not all_in_folders:
        await ctx.send(Message(msg("sticker.empty_pack", pack=folder_label)))
        return

    await _try_send_text(
        ctx.bot,
        ctx.event,
        msg("sticker.collage_progress", pack=folder_label, count=len(all_in_folders)),
        "拼图进度",
    )
    try:
        with ActivityScope("sticker_trigger", "generating", "生成贴纸拼图", description=folder_label):
            jpg_path = await _make_collage(all_in_folders, f"{keyword}_{len(folder_names)}packs")
    except Exception as e:
        logger.exception("[Sticker] 拼图生成失败: %s", e)
        await _notify_sticker_error(ctx.bot, ctx.event, e, "StickerCollage")
        await _try_send_text(ctx.bot, ctx.event, msg("sticker.collage_failed"), "拼图失败提示")
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
            msg("sticker.collage_send_failed"),
            "拼图失败提示",
        )


@command("统计", description="查看当前会话统计")
async def cmd_session_stats(ctx: CommandContext) -> None:
    await ctx.send(Message(format_stats(ctx.event)))


async def cmd_sticker_pack_stats(ctx: CommandContext) -> None:
    await ctx.send(Message("\n".join(_sticker_library_stats_lines(sticker_library.get_state()))))


async def cmd_sticker_pack_list(ctx: CommandContext) -> None:
    await _send_pack_list(ctx.bot, ctx.event, ctx.args.strip())


async def cmd_sticker_pack_preview(ctx: CommandContext) -> None:
    state = sticker_library.get_state()
    if not state.get("packs"):
        await ctx.send(Message(msg("sticker.no_packs")))
        return

    await _try_send_text(ctx.bot, ctx.event, msg("sticker.pack_preview_progress"), "贴纸包预览进度")
    try:
        with ActivityScope("sticker_trigger", "generating", "生成贴纸预览图"):
            preview_path = await _make_pack_preview_image()
        await _send_image(ctx.bot, ctx.event, preview_path, "贴纸包预览")
    except Exception as e:
        logger.exception("[Sticker] 贴纸包预览生成或发送失败: %s", e)
        await _notify_sticker_error(ctx.bot, ctx.event, e, "StickerPackPreview")
        await _try_send_text(ctx.bot, ctx.event, msg("sticker.pack_preview_failed"), "贴纸包预览失败提示")


async def cmd_sticker_pack_help(ctx: CommandContext) -> None:
    await ctx.send(Message(msg("sticker.help")))


async def _call_with_args(ctx: CommandContext, args: str, handler) -> None:
    old_args = ctx.args
    ctx.args = args
    try:
        await handler(ctx)
    finally:
        ctx.args = old_args


def _split_pack_subcommand(args: str) -> tuple[str | None, str]:
    text = args.strip()
    if not text:
        return "help", ""
    parts = text.split(maxsplit=1)
    head = parts[0].casefold()
    subcommand = _PACK_SUBCOMMAND_ALIASES.get(head)
    if subcommand is None:
        return None, text
    return subcommand, parts[1].strip() if len(parts) > 1 else ""


@command("贴纸包", description="贴纸包工具", usage="贴纸包", detail_key="sticker.help")
async def cmd_sticker_pack(ctx: CommandContext) -> None:
    subcommand, rest = _split_pack_subcommand(ctx.args)
    if subcommand == "help":
        await _call_with_args(ctx, rest, cmd_sticker_pack_help)
    elif subcommand == "random":
        await _call_with_args(ctx, rest, cmd_random_sticker)
    elif subcommand == "collage":
        await _call_with_args(ctx, rest, cmd_sticker_collage)
    elif subcommand == "stats":
        await _call_with_args(ctx, rest, cmd_sticker_pack_stats)
    elif subcommand == "list":
        await _call_with_args(ctx, rest, cmd_sticker_pack_list)
    elif subcommand == "preview":
        await _call_with_args(ctx, rest, cmd_sticker_pack_preview)
    else:
        await _call_with_args(ctx, "", cmd_sticker_pack_help)


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

    if count <= 0:
        await _try_send_text(bot, event, msg("sticker.count_min"), "贴纸数量提示")
        return

    picked = random.sample(all_in_folders, min(count, len(all_in_folders)))

    logger.info(f"[Sticker] 关键词 '{keyword}' x{len(picked)} → {[p.name for p in picked]}")

    sent = await _send_many_stickers(bot, event, picked)
    if sent:
        mark_event_handled(event)
        stats_increment(event, "stickers_sent", sent)

    _cleanup_shared_dir()
