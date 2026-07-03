from __future__ import annotations

from typing import Any

from core.config_loader import load_plugin_config

DEFAULT_PROFILE_LIKE_CONFIG: dict[str, Any] = {
    "enabled": True,
    "default_times": 10,
    "max_times": 10,
}


def get_config() -> dict[str, Any]:
    cfg = load_plugin_config("profile_like", DEFAULT_PROFILE_LIKE_CONFIG)
    max_times = _parse_int(cfg.get("max_times"), default=10, minimum=1, maximum=10)
    default_times = _parse_int(cfg.get("default_times"), default=max_times, minimum=1, maximum=max_times)
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "default_times": default_times,
        "max_times": max_times,
    }


def _parse_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return min(max(parsed, minimum), maximum)
