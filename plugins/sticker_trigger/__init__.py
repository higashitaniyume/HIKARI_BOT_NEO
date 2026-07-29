"""
表情包触发插件。

检测消息中的关键词，发送对应贴纸包中的随机表情包。
"""

from __future__ import annotations

import logging
import random

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent

from core.activity_tracker import ActivityScope
from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command, is_command_handled, mark_event_handled
from core.stats_tracker import increment as stats_increment, format_stats
from plugins import sticker_library

from .sending import (
    _log_send_failure,
    _notify_sticker_error,
    _send_file,
    _send_image,
    _send_many_stickers,
    _send_text_forward,
    _try_send_text,
)
from .utils import (
    _cleanup_shared_dir,
    _make_collage,
    _make_pack_preview_image,
)

logger = logging.getLogger("HikariBot.StickerPlugin")

PACK_LIST_PAGE_SIZE = 5

# NapCat 共享目录（NapCat 容器必须挂载此目录）

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

# Matcher：中等优先级，不阻塞 pipeline
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
        with ActivityScope("sticker_trigger", "generating", "生成贴纸预览 PDF"):
            preview_path = await _make_pack_preview_image()
        await _send_file(ctx.bot, ctx.event, preview_path, "贴纸包预览.pdf")
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
