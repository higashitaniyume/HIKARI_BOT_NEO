from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core.config_loader import load_plugin_config

logger = logging.getLogger("HikariBot.RssSubscriber.Config")

CONFIG_PATH = Path("BotData/plugin_configs/rss_subscriber.json")

DEFAULT_RSS_SUBSCRIBER_CONFIG: dict[str, Any] = {
    "enabled": True,
    "timeout_seconds": 20,
    "proxy": "",
    "user_agent": "{bot_name} RSS Reader",
    "max_items": 5,
    "summary_max_chars": 220,
    "max_message_chars": 3500,
    "max_feed_bytes": 2097152,
    "max_state_entries": 1000,
    "subscriptions": [
        {
            "id": "example_news",
            "enabled": False,
            "title": "示例订阅",
            "url": "https://example.com/feed.xml",
            "max_items": 3,
            "include_summary": True,
            "summary_max_chars": 220,
            "only_new": True,
            "send_first_run": True,
        }
    ],
}

_first_load_done = False


def get_config() -> dict[str, Any]:
    global _first_load_done
    cfg = load_plugin_config("rss_subscriber", DEFAULT_RSS_SUBSCRIBER_CONFIG)
    if not _first_load_done:
        _first_load_done = True
        subscriptions = cfg.get("subscriptions") if isinstance(cfg.get("subscriptions"), list) else []
        logger.info(
            "RSS 订阅配置加载完成 -> enabled=%s, subscriptions=%d",
            cfg.get("enabled"),
            len(subscriptions),
        )
    return cfg


def save_config(data: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_config(data)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = CONFIG_PATH.with_name(f"{CONFIG_PATH.name}.{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, CONFIG_PATH)
    return normalized


def normalize_config(data: dict[str, Any]) -> dict[str, Any]:
    current = get_config()
    raw_subscriptions = data.get("subscriptions", current.get("subscriptions", []))
    if not isinstance(raw_subscriptions, list):
        raise ValueError("subscriptions 必须是数组。")

    subscriptions = [_normalize_subscription(item, index) for index, item in enumerate(raw_subscriptions)]
    seen_ids: set[str] = set()
    for item in subscriptions:
        if item["id"] in seen_ids:
            raise ValueError(f"RSS 订阅 ID 重复：{item['id']}")
        seen_ids.add(item["id"])

    return {
        "enabled": _parse_bool(data.get("enabled", current.get("enabled", True))),
        "timeout_seconds": _parse_int(
            data.get("timeout_seconds", current.get("timeout_seconds", 20)),
            20,
            minimum=1,
            maximum=300,
        ),
        "proxy": _parse_str(data.get("proxy", current.get("proxy", "")), max_length=512),
        "user_agent": _parse_str(
            data.get("user_agent", current.get("user_agent", "{bot_name} RSS Reader")),
            "{bot_name} RSS Reader",
            max_length=200,
        )
        or "{bot_name} RSS Reader",
        "max_items": _parse_int(data.get("max_items", current.get("max_items", 5)), 5, minimum=1, maximum=50),
        "summary_max_chars": _parse_int(
            data.get("summary_max_chars", current.get("summary_max_chars", 220)),
            220,
            minimum=0,
            maximum=2000,
        ),
        "max_message_chars": _parse_int(
            data.get("max_message_chars", current.get("max_message_chars", 3500)),
            3500,
            minimum=500,
            maximum=12000,
        ),
        "max_feed_bytes": _parse_int(
            data.get("max_feed_bytes", current.get("max_feed_bytes", 2097152)),
            2097152,
            minimum=65536,
            maximum=10485760,
        ),
        "max_state_entries": _parse_int(
            data.get("max_state_entries", current.get("max_state_entries", 1000)),
            1000,
            minimum=100,
            maximum=20000,
        ),
        "subscriptions": subscriptions,
    }


def find_subscription(identifier: str, config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    target = str(identifier or "").strip()
    if not target:
        return None
    cfg = config or get_config()
    subscriptions = cfg.get("subscriptions") if isinstance(cfg.get("subscriptions"), list) else []
    for item in subscriptions:
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or "").strip() == target:
            return item
    return None


def _normalize_subscription(raw_item: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw_item, dict):
        raise ValueError(f"第 {index + 1} 个 RSS 订阅必须是 JSON 对象。")

    subscription_id = _parse_str(raw_item.get("id"), max_length=80)
    if not subscription_id:
        raise ValueError(f"第 {index + 1} 个 RSS 订阅缺少 ID。")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", subscription_id):
        raise ValueError(f"RSS 订阅 ID 只能包含字母、数字、下划线、短横线和点：{subscription_id}")

    url = _parse_url(raw_item.get("url"), label=f"RSS 订阅 {subscription_id}")
    return {
        "id": subscription_id,
        "enabled": _parse_bool(raw_item.get("enabled", True)),
        "title": _parse_str(raw_item.get("title", subscription_id), subscription_id, max_length=120)
        or subscription_id,
        "url": url,
        "max_items": _parse_int(raw_item.get("max_items", 3), 3, minimum=1, maximum=50),
        "include_summary": _parse_bool(raw_item.get("include_summary", True)),
        "summary_max_chars": _parse_int(
            raw_item.get("summary_max_chars", 220),
            220,
            minimum=0,
            maximum=2000,
        ),
        "only_new": _parse_bool(raw_item.get("only_new", True)),
        "send_first_run": _parse_bool(raw_item.get("send_first_run", True)),
    }


def _parse_url(value: Any, *, label: str) -> str:
    url = _parse_str(value, max_length=2048)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{label} 的 URL 必须是 http(s) 地址。")
    return url


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"1", "true", "yes", "on", "启用", "开启", "是"}


def _parse_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return min(max(parsed, minimum), maximum)


def _parse_str(value: Any, default: str = "", *, max_length: int = 4000) -> str:
    text = str(value if value is not None else default).strip()
    return text[:max_length]
