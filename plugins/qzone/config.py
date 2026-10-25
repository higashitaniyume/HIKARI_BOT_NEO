"""QQ 空间插件配置。"""

from __future__ import annotations

import copy
from typing import Any

from core.access_control import DEFAULT_ACCESS_RULES
from core.config_loader import load_plugin_config

# 说说可见范围：public=公开，friends=好友可见，private=仅自己可见
VISIBLE_VALUES: dict[str, int] = {"public": 1, "friends": 3, "private": 4}
VISIBLE_LABELS: dict[int, str] = {1: "公开", 3: "好友可见", 4: "仅自己"}

DEFAULT_QZONE_CONFIG: dict[str, Any] = {
    "enabled": True,
    # 发说说 未显式指定可见范围时使用
    "default_visible": "public",
    # 单条说说最大字数（QQ 空间本身约 1000 字）
    "max_length": 1000,
    # 「空间动态」默认拉取条数
    "feed_count": 8,
    # 「好友说说」默认拉取条数
    "moods_count": 5,
    # 单次空间请求超时（秒）
    "timeout": 20,
    "permissions": copy.deepcopy(DEFAULT_ACCESS_RULES),
}


def get_config() -> dict[str, Any]:
    cfg = load_plugin_config("qzone", DEFAULT_QZONE_CONFIG)
    default_visible = _normalize_visible(cfg.get("default_visible"))
    return {
        "enabled": _as_bool(cfg.get("enabled"), True),
        "default_visible": default_visible,
        "max_length": _clamp_int(cfg.get("max_length"), default=1000, minimum=1, maximum=5000),
        "feed_count": _clamp_int(cfg.get("feed_count"), default=8, minimum=1, maximum=20),
        "moods_count": _clamp_int(cfg.get("moods_count"), default=5, minimum=1, maximum=20),
        "timeout": _clamp_int(cfg.get("timeout"), default=20, minimum=5, maximum=120),
        "permissions": cfg.get("permissions") if isinstance(cfg.get("permissions"), dict) else {},
    }


def _normalize_visible(value: Any) -> str:
    text = str(value or "").strip().casefold()
    return text if text in VISIBLE_VALUES else "public"


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _clamp_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)
