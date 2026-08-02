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
        # 可选的用户/群维度独立开关（不存在时判定回退到整体 enable）。
        # 旧配置只有 enable，行为不变；新配置可分别控制两个维度。
        for dim in ("user_enable", "group_enable"):
            if dim in raw_list:
                result[list_name][dim] = bool(raw_list[dim])
            else:
                result[list_name].pop(dim, None)
        result[list_name]["user"] = normalize_id_list(raw_list.get("user", []))
        result[list_name]["group"] = normalize_id_list(raw_list.get("group", []))

    admin_id = result["admin_id"]
    if admin_id and admin_id not in result["whitelist"]["user"]:
        result["whitelist"]["user"].append(admin_id)
    return result


def _dimension_enabled(list_cfg: dict[str, Any], dimension: str) -> bool:
    """某维度开关是否启用。新结构用 user_enable/group_enable，缺失时回退整体 enable。"""
    key = "user_enable" if dimension == "user" else "group_enable"
    if key in list_cfg:
        return bool(list_cfg[key])
    return bool(list_cfg.get("enable", False))


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
    wl_user = _dimension_enabled(whitelist, "user")
    bl_user = _dimension_enabled(blacklist, "user")
    wl_group = _dimension_enabled(whitelist, "group")
    bl_group = _dimension_enabled(blacklist, "group")
    allowed: bool | None = None

    if wl_user and sender_id in whitelist["user"]:
        allowed = True
    elif bl_user and sender_id in blacklist["user"]:
        allowed = False
    elif wl_group and group_id and group_id in whitelist["group"]:
        allowed = True
    elif bl_group and group_id and group_id in blacklist["group"]:
        allowed = False

    if allowed is None:
        # 任一启用的白名单维度未命中 → 拒绝（全部未启用时默认放行）
        allowed = True
        if wl_user and sender_id not in whitelist["user"]:
            allowed = False
        elif wl_group and (not group_id or group_id not in whitelist["group"]):
            allowed = False
    return allowed
