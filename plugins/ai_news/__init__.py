"""AI news digest command and push source."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command
from plugins.push_framework import PushContext, PushMessage, register_push_source

from .ai_summary import enhance_digest
from .config import get_config
from .feed import NewsItem, fetch_all_sources, normalize_sources, select_items
from .render import render_digest
from .storage import has_seen_state, mark_seen, unseen_keys

logger = logging.getLogger("HikariBot.AiNews")


@register_push_source(
    "ai_news",
    description="聚合 AI 官方、研究和媒体 RSS，生成一张 AI 最新资讯图片。",
    default_options={
        "max_items": None,
        "max_per_source": None,
        "groups": [],
        "source_ids": [],
        "only_new": None,
        "send_first_run": None,
        "mark_seen": None,
        "include_links": False,
        "ai_summary": None,
        "translate": None,
        "target_language": None,
    },
)
async def build_ai_news_push(ctx: PushContext) -> list[PushMessage]:
    cfg = get_config()
    if not bool(cfg.get("enabled", True)):
        return []

    items = await _build_digest_items(ctx.options, now=ctx.now, default_mark_seen=not ctx.force)
    if not items:
        return []

    items, ai_summary = await enhance_digest(items, config=cfg, options=ctx.options)
    path = await render_digest(items, config=cfg, generated_at=ctx.now, ai_summary=ai_summary)
    messages = [PushMessage(Message(MessageSegment.image(path.resolve().as_uri())), "AI 资讯图片")]
    if _parse_bool(ctx.options.get("include_links"), default=False):
        links = _format_links(items)
        if links:
            messages.append(PushMessage(Message(links), "AI 资讯链接"))
    return messages


@command(
    "ai资讯",
    aliases=("AI资讯", "ai新闻", "AI新闻", "ai日报", "AI日报"),
    description="生成 AI 最新资讯图片",
    usage="ai资讯 [数量] [刷新]",
    detail_key="ai_news.help",
    require_tome=True,
)
async def handle_ai_news(ctx: CommandContext) -> None:
    if not bool(get_config().get("enabled", True)):
        await ctx.send(Message(msg("ai_news.disabled")))
        return

    max_items = _parse_count(ctx.args)
    options = _manual_options(ctx.args)
    options.update({"max_items": max_items, "only_new": False, "mark_seen": False})
    await ctx.send(Message(msg("ai_news.fetching")))
    try:
        items = await _build_digest_items(
            options,
            now=datetime.now().astimezone(),
            default_mark_seen=False,
        )
    except Exception as e:
        logger.exception("[AiNews] 手动生成失败: %s", e)
        await ctx.send(Message(msg("ai_news.failed")))
        return

    if not items:
        await ctx.send(Message(msg("ai_news.empty")))
        return

    cfg = get_config()
    items, ai_summary = await enhance_digest(items, config=cfg, options=options)
    path = await render_digest(items, config=cfg, generated_at=datetime.now().astimezone(), ai_summary=ai_summary)
    await ctx.send(Message(MessageSegment.image(path.resolve().as_uri())))


async def _build_digest_items(
    options: dict[str, Any],
    *,
    now: datetime,
    default_mark_seen: bool,
) -> list[NewsItem]:
    cfg = get_config()
    sources = normalize_sources(cfg, options)
    if not sources:
        return []

    fetched_items = await fetch_all_sources(sources, cfg)
    if not fetched_items:
        return []

    max_items = _parse_int(options.get("max_items"), default=_parse_int(cfg.get("max_items"), default=10, minimum=1, maximum=50), minimum=1, maximum=50)
    max_age_hours = _parse_int(
        options.get("max_age_hours"),
        default=_parse_int(cfg.get("max_age_hours"), default=168, minimum=0, maximum=24 * 90),
        minimum=0,
        maximum=24 * 90,
    )
    max_per_source = _parse_int(
        options.get("max_per_source"),
        default=_parse_int(cfg.get("max_per_source"), default=3, minimum=1, maximum=50),
        minimum=1,
        maximum=50,
    )
    if len(sources) == 1:
        max_per_source = max(max_per_source, max_items)
    keyword_boosts = cfg.get("keyword_boosts") if isinstance(cfg.get("keyword_boosts"), list) else []
    candidates = select_items(
        fetched_items,
        max_items=max(1, max_items * 3),
        max_age_hours=max_age_hours,
        now=now,
        keyword_boosts=[str(item) for item in keyword_boosts],
        max_per_source=max_per_source,
    )

    scope = _state_scope(options)
    only_new = _parse_bool(options.get("only_new"), default=bool(cfg.get("only_new", True)))
    send_first_run = _parse_bool(options.get("send_first_run"), default=bool(cfg.get("send_first_run", True)))
    mark_seen_enabled = _parse_bool(options.get("mark_seen"), default=default_mark_seen)

    selected = candidates
    if only_new:
        had_seen_state = has_seen_state(scope)
        unseen = set(unseen_keys(scope, [item.key for item in candidates]))
        selected = [item for item in candidates if item.key in unseen]
        if not had_seen_state and not send_first_run:
            if mark_seen_enabled:
                _mark_seen_items(scope, candidates, cfg)
            return []

    selected = selected[:max_items]
    if mark_seen_enabled:
        _mark_seen_items(scope, candidates, cfg)
    return selected


def _mark_seen_items(scope: str, items: list[NewsItem], cfg: dict[str, Any]) -> None:
    max_entries = _parse_int(cfg.get("max_state_entries"), default=5000, minimum=100, maximum=50000)
    mark_seen(scope, [item.key for item in items], max_entries=max_entries)


def _state_scope(options: dict[str, Any]) -> str:
    scope = str(options.get("state_scope") or "default").strip()
    if scope and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", scope):
        return scope
    return "default"


def _format_links(items: list[NewsItem]) -> str:
    lines = ["AI 资讯链接："]
    for index, item in enumerate(items, start=1):
        if not item.link:
            continue
        lines.append(f"{index}. {item.title}\n{item.link}")
    return "\n\n".join(lines) if len(lines) > 1 else ""


def _parse_count(text: str) -> int | None:
    match = re.search(r"\b(\d{1,2})\b", str(text or ""))
    if not match:
        return None
    return _parse_int(match.group(1), default=10, minimum=1, maximum=20)


def _manual_options(text: str) -> dict[str, Any]:
    normalized = str(text or "").strip().casefold()
    options: dict[str, Any] = {}
    if any(token in normalized for token in ("总结", "摘要", "翻译", "ai", "AI".casefold())):
        options["ai_summary"] = True
    if any(token in normalized for token in ("原文", "不总结", "noai", "no-ai")):
        options["ai_summary"] = False
    return options


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
