"""RSS subscription commands and push source."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

from nonebot.adapters.onebot.v11 import Message

from core.ai_tool_registry import AIToolContext, register_ai_tool
from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command
from core.config_loader import load_main_config
from plugins.push_framework import PushContext, PushMessage, register_push_source

from .config import find_subscription, get_config, save_config
from .feed import RssFeed, RssFeedError, fetch_feed, format_feed_message
from .storage import has_seen_state, mark_seen, unseen_keys

logger = logging.getLogger("HikariBot.RssSubscriber")


@register_push_source(
    "rss_feed",
    description="推送 RSS/Atom 订阅更新，可通过 subscription_id 引用 rss_subscriber 配置。",
    default_options={
        "subscription_id": "",
        "url": "",
        "max_items": 3,
        "include_summary": True,
        "only_new": True,
        "send_first_run": True,
        "mark_seen": None,
    },
)
async def build_rss_push(ctx: PushContext) -> list[PushMessage]:
    if not bool(get_config().get("enabled", True)):
        return []

    subscription = _resolve_push_subscription(ctx.options)
    if subscription is None or not bool(subscription.get("enabled", True)):
        return []

    feed, entries = await _fetch_entries_for_options(subscription, ctx.options, default_mark_seen=not ctx.force)
    if not entries:
        return []

    include_summary = _parse_bool(ctx.options.get("include_summary"), default=bool(subscription.get("include_summary", True)))
    summary_max_chars = _parse_int(
        ctx.options.get("summary_max_chars", subscription.get("summary_max_chars", get_config().get("summary_max_chars", 220))),
        default=220,
        minimum=0,
        maximum=2000,
    )
    max_message_chars = _parse_int(
        ctx.options.get("max_message_chars", get_config().get("max_message_chars", 3500)),
        default=3500,
        minimum=500,
        maximum=12000,
    )
    text = format_feed_message(
        feed,
        entries,
        include_summary=include_summary,
        summary_max_chars=summary_max_chars,
        max_message_chars=max_message_chars,
    )
    return [PushMessage(Message(text), f"RSS {subscription['id']}")] if text else []


@register_ai_tool(
    "rss_latest",
    plugin_name="rss_subscriber",
    description="Read the latest entries from a configured RSS subscription ID or an explicit RSS/Atom URL without changing seen state.",
    parameters={
        "type": "object",
        "properties": {
            "subscription_id": {
                "type": "string",
                "description": "Configured rss_subscriber subscription id.",
            },
            "url": {
                "type": "string",
                "description": "RSS/Atom URL to read when no subscription_id is supplied.",
            },
            "max_items": {
                "type": "integer",
                "description": "Maximum number of entries to return.",
                "minimum": 1,
                "maximum": 20,
            },
            "include_summary": {
                "type": "boolean",
                "description": "Whether summaries should be included in returned entries.",
            },
        },
        "additionalProperties": False,
    },
)
async def ai_tool_rss_latest(context: AIToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    cfg = get_config()
    if not bool(cfg.get("enabled", True)):
        return {"error": "rss_subscriber is disabled"}

    target = str(arguments.get("subscription_id") or "").strip()
    url = str(arguments.get("url") or "").strip()
    subscription = _resolve_command_subscription(target or url, cfg)
    if subscription is None:
        return {"error": "subscription_id or url is required"}
    if not bool(subscription.get("enabled", True)) and target:
        return {"subscription_id": target, "error": "subscription is disabled"}

    max_items = _parse_int(
        arguments.get("max_items", subscription.get("max_items", cfg.get("max_items", 5))),
        default=5,
        minimum=1,
        maximum=20,
    )
    include_summary = _parse_bool(arguments.get("include_summary"), default=bool(subscription.get("include_summary", True)))
    summary_max_chars = _parse_int(
        subscription.get("summary_max_chars", cfg.get("summary_max_chars", 220)),
        default=220,
        minimum=0,
        maximum=2000,
    )

    try:
        feed = await fetch_feed(str(subscription["url"]), cfg)
    except RssFeedError as e:
        logger.warning("[RSS] AI Tool 读取失败 subscription=%s: %s", subscription.get("id"), e)
        return {"subscription_id": subscription.get("id", ""), "error": str(e)}

    entries = feed.entries[:max_items]
    return {
        "subscription_id": subscription.get("id", ""),
        "feed_title": feed.title,
        "feed_url": feed.url,
        "count": len(entries),
        "entries": [
            {
                "title": entry.title,
                "link": entry.link,
                "summary": entry.summary[:summary_max_chars] if include_summary and summary_max_chars > 0 else "",
                "published": entry.published,
            }
            for entry in entries
        ],
    }


@command(
    "rss",
    aliases=("RSS", "订阅", "rss订阅"),
    description="查看 RSS 订阅",
    usage="rss [列表|看|添加|删除|开启|关闭|测试]",
    detail_key="rss.help",
    require_tome=True,
)
async def handle_rss(ctx: CommandContext) -> None:
    args = ctx.args.strip()
    if not args or args.casefold() in {"帮助", "help", "菜单"}:
        await ctx.send(Message(msg("rss.help")))
        return

    action, _, rest = args.partition(" ")
    normalized = action.strip().casefold()

    if normalized in {"列表", "list", "ls"}:
        await ctx.send(Message(_format_subscription_list()))
        return

    if normalized in {"看", "查看", "latest", "show", "read"}:
        await _send_latest(ctx, rest.strip(), require_enabled=True)
        return

    if normalized in {"测试", "test", "试读"}:
        if not _is_superuser(ctx):
            await ctx.send(Message(msg("rss.permission_denied")))
            return
        await _send_latest(ctx, rest.strip(), require_enabled=False)
        return

    if normalized in {"添加", "add", "新增"}:
        await _handle_add(ctx, rest.strip())
        return

    if normalized in {"删除", "remove", "delete", "del"}:
        await _handle_delete(ctx, rest.strip())
        return

    if normalized in {"开启", "enable", "启用"}:
        await _handle_toggle(ctx, rest.strip(), enabled=True)
        return

    if normalized in {"关闭", "disable", "停用"}:
        await _handle_toggle(ctx, rest.strip(), enabled=False)
        return

    await _send_latest(ctx, args, require_enabled=True)


async def _send_latest(ctx: CommandContext, text: str, *, require_enabled: bool) -> None:
    if require_enabled and not bool(get_config().get("enabled", True)):
        await ctx.send(Message(msg("rss.disabled")))
        return

    target, count = _parse_target_and_count(text)
    if not target:
        await ctx.send(Message(msg("rss.show_usage")))
        return

    cfg = get_config()
    subscription = _resolve_command_subscription(target, cfg)
    if subscription is None:
        await ctx.send(Message(msg("rss.not_found", subscription_id=target)))
        return
    if require_enabled and not bool(subscription.get("enabled", True)):
        await ctx.send(Message(msg("rss.subscription_disabled", subscription_id=subscription["id"])))
        return

    await ctx.send(Message(msg("rss.fetching", subscription_id=subscription["id"])))
    try:
        feed = await fetch_feed(str(subscription["url"]), cfg)
    except RssFeedError as e:
        logger.warning("[RSS] 手动读取失败 subscription=%s: %s", subscription["id"], e)
        await ctx.send(Message(msg("rss.fetch_failed", error=e)))
        return

    max_items = count or _parse_int(subscription.get("max_items", cfg.get("max_items", 5)), default=5, minimum=1, maximum=50)
    entries = feed.entries[:max_items]
    if not entries:
        await ctx.send(Message(msg("rss.empty", subscription_id=subscription["id"])))
        return

    text = format_feed_message(
        feed,
        entries,
        include_summary=bool(subscription.get("include_summary", True)),
        summary_max_chars=_parse_int(
            subscription.get("summary_max_chars", cfg.get("summary_max_chars", 220)),
            default=220,
            minimum=0,
            maximum=2000,
        ),
        max_message_chars=_parse_int(cfg.get("max_message_chars", 3500), default=3500, minimum=500, maximum=12000),
    )
    await ctx.send(Message(text))


async def _handle_add(ctx: CommandContext, text: str) -> None:
    if not _is_superuser(ctx):
        await ctx.send(Message(msg("rss.permission_denied")))
        return

    parts = text.split(maxsplit=2)
    if len(parts) < 2:
        await ctx.send(Message(msg("rss.add_usage")))
        return
    subscription_id, url = parts[0].strip(), parts[1].strip()
    title = parts[2].strip() if len(parts) > 2 else subscription_id

    cfg = get_config()
    subscriptions = cfg.get("subscriptions") if isinstance(cfg.get("subscriptions"), list) else []
    if find_subscription(subscription_id, cfg) is not None:
        await ctx.send(Message(msg("rss.already_exists", subscription_id=subscription_id)))
        return
    subscriptions.append(
        {
            "id": subscription_id,
            "enabled": True,
            "title": title,
            "url": url,
            "max_items": 3,
            "include_summary": True,
            "summary_max_chars": int(cfg.get("summary_max_chars") or 220),
            "only_new": True,
            "send_first_run": True,
        }
    )
    cfg["subscriptions"] = subscriptions

    try:
        save_config(cfg)
    except ValueError as e:
        await ctx.send(Message(str(e)))
        return
    await ctx.send(Message(msg("rss.add_success", subscription_id=subscription_id)))


async def _handle_delete(ctx: CommandContext, subscription_id: str) -> None:
    if not _is_superuser(ctx):
        await ctx.send(Message(msg("rss.permission_denied")))
        return
    subscription_id = subscription_id.strip()
    if not subscription_id:
        await ctx.send(Message(msg("rss.id_usage")))
        return

    cfg = get_config()
    subscriptions = cfg.get("subscriptions") if isinstance(cfg.get("subscriptions"), list) else []
    next_subscriptions = [item for item in subscriptions if not isinstance(item, dict) or item.get("id") != subscription_id]
    if len(next_subscriptions) == len(subscriptions):
        await ctx.send(Message(msg("rss.not_found", subscription_id=subscription_id)))
        return
    cfg["subscriptions"] = next_subscriptions
    save_config(cfg)
    await ctx.send(Message(msg("rss.delete_success", subscription_id=subscription_id)))


async def _handle_toggle(ctx: CommandContext, subscription_id: str, *, enabled: bool) -> None:
    if not _is_superuser(ctx):
        await ctx.send(Message(msg("rss.permission_denied")))
        return
    subscription_id = subscription_id.strip()
    if not subscription_id:
        await ctx.send(Message(msg("rss.id_usage")))
        return

    cfg = get_config()
    subscriptions = cfg.get("subscriptions") if isinstance(cfg.get("subscriptions"), list) else []
    found = False
    for item in subscriptions:
        if isinstance(item, dict) and item.get("id") == subscription_id:
            item["enabled"] = enabled
            found = True
            break
    if not found:
        await ctx.send(Message(msg("rss.not_found", subscription_id=subscription_id)))
        return
    save_config(cfg)
    key = "rss.enable_success" if enabled else "rss.disable_success"
    await ctx.send(Message(msg(key, subscription_id=subscription_id)))


async def _fetch_entries_for_options(
    subscription: dict[str, Any],
    options: dict[str, Any],
    *,
    default_mark_seen: bool,
) -> tuple[RssFeed, list]:
    cfg = get_config()
    feed = await fetch_feed(str(subscription["url"]), cfg)
    max_items = _parse_int(
        options.get("max_items", subscription.get("max_items", cfg.get("max_items", 5))),
        default=5,
        minimum=1,
        maximum=50,
    )
    only_new = _parse_bool(options.get("only_new"), default=bool(subscription.get("only_new", True)))
    send_first_run = _parse_bool(options.get("send_first_run"), default=bool(subscription.get("send_first_run", True)))
    mark_seen_enabled = _parse_bool(options.get("mark_seen"), default=default_mark_seen)
    subscription_id = str(subscription["id"])

    entries = feed.entries
    if only_new:
        had_seen_state = has_seen_state(subscription_id)
        unseen = set(unseen_keys(subscription_id, [entry.key for entry in entries]))
        entries = [entry for entry in entries if entry.key in unseen]
        if not had_seen_state and not send_first_run:
            if mark_seen_enabled:
                mark_seen(subscription_id, [entry.key for entry in feed.entries], max_entries=_max_state_entries(cfg))
            return feed, []

    selected = entries[:max_items]
    if mark_seen_enabled:
        mark_seen(subscription_id, [entry.key for entry in feed.entries], max_entries=_max_state_entries(cfg))
    return feed, selected


def _format_subscription_list() -> str:
    cfg = get_config()
    subscriptions = cfg.get("subscriptions") if isinstance(cfg.get("subscriptions"), list) else []
    if not subscriptions:
        return msg("rss.list_empty")

    lines = [msg("rss.list_header")]
    for item in subscriptions:
        if not isinstance(item, dict):
            continue
        enabled = "开启" if bool(item.get("enabled", True)) else "关闭"
        lines.append(
            msg(
                "rss.list_line",
                subscription_id=item.get("id", ""),
                title=item.get("title") or item.get("id", ""),
                enabled=enabled,
                url=item.get("url", ""),
            )
        )
    return "\n".join(lines)


def _resolve_push_subscription(options: dict[str, Any]) -> dict[str, Any] | None:
    cfg = get_config()
    subscription_id = str(options.get("subscription_id") or options.get("id") or "").strip()
    if subscription_id:
        return find_subscription(subscription_id, cfg)

    url = str(options.get("url") or "").strip()
    if url:
        return {
            "id": _ad_hoc_subscription_id(url),
            "enabled": True,
            "title": str(options.get("title") or "RSS 订阅").strip() or "RSS 订阅",
            "url": url,
            "max_items": _parse_int(options.get("max_items", cfg.get("max_items", 5)), default=5, minimum=1, maximum=50),
            "include_summary": _parse_bool(options.get("include_summary"), default=True),
            "summary_max_chars": _parse_int(
                options.get("summary_max_chars", cfg.get("summary_max_chars", 220)),
                default=220,
                minimum=0,
                maximum=2000,
            ),
            "only_new": _parse_bool(options.get("only_new"), default=True),
            "send_first_run": _parse_bool(options.get("send_first_run"), default=True),
        }

    subscriptions = [item for item in cfg.get("subscriptions", []) if isinstance(item, dict)]
    enabled_subscriptions = [item for item in subscriptions if bool(item.get("enabled", True))]
    return enabled_subscriptions[0] if len(enabled_subscriptions) == 1 else None


def _resolve_command_subscription(target: str, cfg: dict[str, Any]) -> dict[str, Any] | None:
    if _looks_like_url(target):
        return {
            "id": _ad_hoc_subscription_id(target),
            "enabled": True,
            "title": "RSS 订阅",
            "url": target,
            "max_items": int(cfg.get("max_items") or 5),
            "include_summary": True,
            "summary_max_chars": int(cfg.get("summary_max_chars") or 220),
            "only_new": False,
            "send_first_run": True,
        }
    return find_subscription(target, cfg)


def _parse_target_and_count(text: str) -> tuple[str, int | None]:
    parts = str(text or "").split()
    if not parts:
        return "", None
    count = None
    if parts[-1].isdigit():
        count = _parse_int(parts[-1], default=0, minimum=1, maximum=50)
        parts = parts[:-1]
    return " ".join(parts).strip(), count


def _ad_hoc_subscription_id(url: str) -> str:
    parsed = urlparse(url)
    raw = f"{parsed.netloc}{parsed.path}".strip("/") or "adhoc"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._-")
    return (safe or "adhoc")[:80]


def _looks_like_url(value: str) -> bool:
    parsed = urlparse(str(value or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _max_state_entries(cfg: dict[str, Any]) -> int:
    return _parse_int(cfg.get("max_state_entries", 1000), default=1000, minimum=100, maximum=20000)


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


def _is_superuser(ctx: CommandContext) -> bool:
    try:
        superuser_id = str(load_main_config().get("bot", {}).get("superuser_id") or "").strip()
        return bool(superuser_id) and str(ctx.event.get_user_id()).strip() == superuser_id
    except Exception:
        return False
