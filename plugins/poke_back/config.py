from __future__ import annotations

from typing import Any

from core.config_loader import load_plugin_config

DEFAULT_POKE_BACK_CONFIG: dict[str, Any] = {
    "enabled": True,
    "group_enabled": True,
    "private_enabled": True,
}


def get_config() -> dict[str, Any]:
    cfg = load_plugin_config("poke_back", DEFAULT_POKE_BACK_CONFIG)
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "group_enabled": bool(cfg.get("group_enabled", True)),
        "private_enabled": bool(cfg.get("private_enabled", True)),
    }
