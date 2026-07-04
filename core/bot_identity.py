from __future__ import annotations

from typing import Any

from core.config_loader import DEFAULT_MAIN_CONFIG, load_main_config

_FALLBACK_BOT_NAME = str(DEFAULT_MAIN_CONFIG.get("bot", {}).get("name") or "HikariBotNeo")
_LEGACY_BOT_NAME_TOKENS = (
    "HIKARI_BOT_NEO",
    "HIKARI BOT NEO",
    "HikariBotNeo",
    "HIKARI BOT",
    "Hikari Bot",
    "HIKARI",
)


def get_bot_name() -> str:
    """Return the configured bot display name from BotData/config.json."""
    try:
        config = load_main_config()
    except Exception:
        return _FALLBACK_BOT_NAME

    bot_cfg = config.get("bot") if isinstance(config.get("bot"), dict) else {}
    name = str(bot_cfg.get("name") or "").strip()
    return name or _FALLBACK_BOT_NAME


def format_bot_name_text(value: Any, **kwargs: Any) -> str:
    """Format text with the configured bot name and migrate legacy display names."""
    text = str(value)
    needs_bot_name = "{bot_name}" in text or any(legacy in text for legacy in _LEGACY_BOT_NAME_TOKENS)

    if needs_bot_name:
        bot_name = get_bot_name()
        for legacy in _LEGACY_BOT_NAME_TOKENS:
            text = text.replace(legacy, bot_name)
        kwargs = {"bot_name": bot_name, **kwargs}

    try:
        return text.format(**kwargs) if kwargs else text
    except Exception:
        return text


def bot_user_agent(component: str = "") -> str:
    component = str(component or "").strip()
    if not component:
        return get_bot_name()
    return f"{get_bot_name()} {component}"
