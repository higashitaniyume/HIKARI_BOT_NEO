"""Zhihu hot list command and push source."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from core.ai_tool_registry import AIToolContext, register_ai_tool
from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command
from plugins.push_framework import PushContext, PushMessage, register_push_source

from .api import ZhihuHotClient, ZhihuHotError, ZhihuHotItem
from .config import get_config
from .render import render_hot_list

logger = logging.getLogger("HikariBot.ZhihuHot")


@register_push_source(
    "zhihu_hot",
    description="抓取知乎热搜榜，生成一张知乎热搜图片。",
    default_options={
        "max_items": None,
        "include_links": False,
        "force_refresh": False,
    },
)
async def build_zhihu_hot_push(ctx: PushContext) -> list[PushMessage]:
    cfg = get_config()
    if not bool(cfg.get("enabled", True)):
        return []

    items = await _build_hot_items(ctx.options, force=ctx.force)
    if not items:
        return []

    path = await render_hot_list(items, config=cfg, generated_at=ctx.now)
    messages = [PushMessage(Message(MessageSegment.image(path.resolve().as_uri())), "知乎热搜图片")]
    if _parse_bool(ctx.options.get("include_links"), default=False):
        links = _format_links(items)
        if links:
            messages.append(PushMessage(Message(links), "知乎热搜链接"))
    return messages


@register_ai_tool(
    "zhihu_hot_list",
    plugin_name="zhihu_hot",
    description="Fetch the current Zhihu hot list and return ranked questions with heat, excerpt, and URLs.",
    parameters={
        "type": "object",
        "properties": {
            "max_items": {
                "type": "integer",
                "description": "Maximum number of hot questions to return.",
                "minimum": 1,
                "maximum": 30,
            },
            "force_refresh": {
                "type": "boolean",
                "description": "Whether to bypass the short in-memory cache.",
            },
        },
        "additionalProperties": False,
    },
)
async def ai_tool_zhihu_hot_list(context: AIToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    cfg = get_config()
    if not bool(cfg.get("enabled", True)):
        return {"error": "zhihu_hot is disabled"}
    max_items = _parse_int(
        arguments.get("max_items"),
        default=_parse_int(cfg.get("max_items"), default=15, minimum=1, maximum=30),
        minimum=1,
        maximum=30,
    )
    force_refresh = _parse_bool(arguments.get("force_refresh"), default=False)
    try:
        items = await _build_hot_items({"max_items": max_items, "force_refresh": force_refresh}, force=force_refresh)
    except Exception as e:
        logger.warning("[ZhihuHot] AI Tool 读取失败: %s", e)
        return {"error": str(e)}
    return {
        "count": len(items),
        "items": [
            {
                "rank": item.rank,
                "title": item.title,
                "url": item.url,
                "heat": item.heat,
                "excerpt": item.excerpt,
                "answer_count": item.answer_count,
                "follower_count": item.follower_count,
                "question_id": item.question_id,
                "trend": item.trend,
                "debut": item.debut,
            }
            for item in items[:max_items]
        ],
    }


@command(
    "知乎热搜",
    aliases=("知乎热榜", "知乎日报", "zhihu热搜", "zhihu_hot"),
    description="生成知乎热搜图片",
    usage="知乎热搜 [数量] [刷新] [链接]",
    detail_key="zhihu_hot.help",
    require_tome=True,
)
async def handle_zhihu_hot(ctx: CommandContext) -> None:
    if _is_help(ctx.args):
        await ctx.send(Message(msg("zhihu_hot.help")))
        return
    if not bool(get_config().get("enabled", True)):
        await ctx.send(Message(msg("zhihu_hot.disabled")))
        return

    options = _manual_options(ctx.args)
    await ctx.send(Message(msg("zhihu_hot.fetching")))
    try:
        items = await _build_hot_items(options, force=_parse_bool(options.get("force_refresh"), default=False))
    except Exception as e:
        logger.exception("[ZhihuHot] 手动生成失败: %s", e)
        await ctx.send(Message(msg("zhihu_hot.failed")))
        return

    if not items:
        await ctx.send(Message(msg("zhihu_hot.empty")))
        return

    cfg = get_config()
    path = await render_hot_list(items, config=cfg, generated_at=datetime.now().astimezone())
    await ctx.send(Message(MessageSegment.image(path.resolve().as_uri())))
    if _parse_bool(options.get("include_links"), default=False):
        links = _format_links(items)
        if links:
            await ctx.send(Message(links))


async def _build_hot_items(options: dict[str, Any], *, force: bool = False) -> list[ZhihuHotItem]:
    cfg = get_config()
    max_items = _parse_int(
        options.get("max_items"),
        default=_parse_int(cfg.get("max_items"), default=15, minimum=1, maximum=30),
        minimum=1,
        maximum=30,
    )
    force_refresh = force or _parse_bool(options.get("force_refresh"), default=False)
    client = ZhihuHotClient(cfg)
    return await client.fetch_hot_items(max_items=max_items, force_refresh=force_refresh)


def _manual_options(text: str) -> dict[str, Any]:
    options: dict[str, Any] = {"max_items": _parse_count(text)}
    normalized = str(text or "").strip().casefold()
    if any(token in normalized for token in ("刷新", "refresh", "reload")):
        options["force_refresh"] = True
    if any(token in normalized for token in ("链接", "link", "links", "url")):
        options["include_links"] = True
    return options


def _is_help(text: str) -> bool:
    return str(text or "").strip().casefold() in {"帮助", "help", "菜单", "-h", "--help"}


def _format_links(items: list[ZhihuHotItem]) -> str:
    lines = ["知乎热搜链接："]
    for item in items:
        if not item.url:
            continue
        lines.append(f"{item.rank}. {item.title}\n{item.url}")
    return "\n\n".join(lines) if len(lines) > 1 else ""


def _parse_count(text: str) -> int | None:
    match = re.search(r"\b(\d{1,2})\b", str(text or ""))
    if not match:
        return None
    return _parse_int(match.group(1), default=15, minimum=1, maximum=30)


def _parse_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"1", "true", "yes", "on", "启用", "开启", "是"}


def _parse_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return min(max(parsed, minimum), maximum)


__all__ = [
    "ZhihuHotClient",
    "ZhihuHotError",
    "ZhihuHotItem",
    "build_zhihu_hot_push",
    "handle_zhihu_hot",
]
