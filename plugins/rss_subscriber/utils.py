"""Utility functions for RSS subscriber."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from core.bot_messages import get_message as msg
from core.command_router import CommandContext
from core.config_loader import load_main_config

from .config import find_subscription, get_config
from .feed import RssFeed, fetch_feed
from .storage import has_seen_state, mark_seen, unseen_keys


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
