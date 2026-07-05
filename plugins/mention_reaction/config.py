from __future__ import annotations

from typing import Any

from core.config_loader import load_plugin_config

DEFAULT_MENTION_REACTION_CONFIG: dict[str, Any] = {
    "enabled": True,
    "group_enabled": True,
    "emoji_ids": ["66"],
    "random": False,
    "allowed_groups": [],
    "ignored_users": [],
}


def get_config() -> dict[str, Any]:
    cfg = load_plugin_config("mention_reaction", DEFAULT_MENTION_REACTION_CONFIG)
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "group_enabled": bool(cfg.get("group_enabled", True)),
        "emoji_ids": _normalize_emoji_ids(cfg.get("emoji_ids")),
        "random": bool(cfg.get("random", False)),
        "allowed_groups": _normalize_str_list(cfg.get("allowed_groups")),
        "ignored_users": _normalize_str_list(cfg.get("ignored_users")),
    }


def _normalize_emoji_ids(value: Any) -> list[str]:
    if isinstance(value, (str, int)):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        values = DEFAULT_MENTION_REACTION_CONFIG["emoji_ids"]

    emoji_ids: list[str] = []
    for item in values:
        text = str(item).strip()
        if text:
            emoji_ids.append(text)
    return emoji_ids


def _normalize_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := str(item).strip())]
