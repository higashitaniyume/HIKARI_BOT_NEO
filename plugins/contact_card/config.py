from __future__ import annotations

from typing import Any

from core.config_loader import load_plugin_config

DEFAULT_CONTACT_CARD_CONFIG: dict[str, Any] = {
    "enabled": True,
    # QQ 号位数范围：QQ 从 10000 起号，uin 为 32 位无符号整数（最长 10 位），
    # 这里留出余量到 11 位，避免把正常号码挡在外面。
    "min_digits": 5,
    "max_digits": 11,
}


def get_config() -> dict[str, Any]:
    cfg = load_plugin_config("contact_card", DEFAULT_CONTACT_CARD_CONFIG)
    min_digits = _parse_int(cfg.get("min_digits"), default=5, minimum=1, maximum=20)
    max_digits = _parse_int(cfg.get("max_digits"), default=11, minimum=min_digits, maximum=20)
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "min_digits": min_digits,
        "max_digits": max_digits,
    }


def _parse_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return min(max(parsed, minimum), maximum)
