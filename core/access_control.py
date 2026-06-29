"""Shared per-plugin allow/deny list checks."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent


DEFAULT_ACCESS_RULES: dict[str, Any] = {
    "admin_id": "",
    "whitelist": {
        "enable": False,
        "user": [],
        "group": [],
    },
    "blacklist": {
        "enable": False,
        "user": [],
        "group": [],
    },
}


def normalize_id_list(value: Any) -> list[str]:
    """Normalize QQ/group IDs to unique non-empty strings."""
    if isinstance(value, str):
        raw_items = value.replace("，", ",").replace("\n", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def normalize_access_rules(value: Any) -> dict[str, Any]:
    """Return a complete access-rule object while preserving the shared schema."""
    source = value if isinstance(value, dict) else {}
    result = deepcopy(DEFAULT_ACCESS_RULES)
    result["admin_id"] = str(source.get("admin_id") or "").strip()

    for list_name in ("whitelist", "blacklist"):
        raw_list = source.get(list_name) if isinstance(source.get(list_name), dict) else {}
        result[list_name]["enable"] = bool(raw_list.get("enable", False))
        result[list_name]["user"] = normalize_id_list(raw_list.get("user", []))
        result[list_name]["group"] = normalize_id_list(raw_list.get("group", []))

    admin_id = result["admin_id"]
    if admin_id and admin_id not in result["whitelist"]["user"]:
        result["whitelist"]["user"].append(admin_id)
    return result


def is_event_allowed(config: dict[str, Any], event: MessageEvent) -> bool:
    """Check whether an event is allowed by a plugin config's permissions block."""
    rules = normalize_access_rules(config.get("permissions", {}))
    sender_id = str(event.get_user_id() or "").strip()
    is_private = not isinstance(event, GroupMessageEvent)
    group_id = "" if is_private else str(event.group_id or "").strip()

    if rules["admin_id"] and sender_id == rules["admin_id"]:
        return True

    whitelist = rules["whitelist"]
    blacklist = rules["blacklist"]
    allowed: bool | None = None

    if whitelist["enable"] and sender_id in whitelist["user"]:
        allowed = True
    elif blacklist["enable"] and sender_id in blacklist["user"]:
        allowed = False
    elif whitelist["enable"] and group_id and group_id in whitelist["group"]:
        allowed = True
    elif blacklist["enable"] and group_id and group_id in blacklist["group"]:
        allowed = False

    if allowed is None:
        allowed = not whitelist["enable"]
    return allowed
