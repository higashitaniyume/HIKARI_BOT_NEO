"""Lifecycle-oriented logging helpers for HikariBot."""

from __future__ import annotations

import logging
import os
import platform
import sys
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from nonebot.adapters import Bot as BaseBot

logger = logging.getLogger("HikariBot.Lifecycle")

_SECRET_QUERY_KEYS = {
    "access_token",
    "api_key",
    "key",
    "secret",
    "sign",
    "token",
}


def redact_url(url: Any) -> str:
    """Return a URL safe for logs by masking credentials and secret query values."""
    raw = str(url or "")
    if not raw:
        return ""

    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw

    netloc = parts.netloc
    if parts.username or parts.password:
        host = parts.hostname or ""
        try:
            port = f":{parts.port}" if parts.port else ""
        except ValueError:
            port = ""
        user = parts.username or "***"
        netloc = f"{user}:***@{host}{port}"

    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    redacted_query = urlencode(
        [
            (key, "***" if key.casefold() in _SECRET_QUERY_KEYS else value)
            for key, value in query_pairs
        ]
    )
    return urlunsplit((parts.scheme, netloc, parts.path, redacted_query, parts.fragment))


def mask_identifier(value: Any) -> str:
    """Mask a QQ/user identifier while keeping enough shape for troubleshooting."""
    raw = str(value or "").strip()
    if not raw:
        return "<empty>"
    if len(raw) <= 6:
        return raw
    return f"{raw[:3]}***{raw[-3:]}"


def describe_event(event: Any, text: str | None = None) -> str:
    """Build a compact OneBot/NoneBot event summary for log lines."""
    parts = [f"event={event.__class__.__name__}"]
    for attr, label in (
        ("message_type", "type"),
        ("sub_type", "sub_type"),
        ("detail_type", "detail_type"),
        ("group_id", "group_id"),
        ("user_id", "user_id"),
        ("message_id", "message_id"),
    ):
        value = getattr(event, attr, None)
        if value is not None:
            parts.append(f"{label}={value}")
    if text is not None:
        parts.append(f"text_len={len(text)}")
    return " ".join(parts)


def preview_text(text: str, *, max_chars: int = 80) -> str:
    """Return a single-line text preview for logs."""
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= max_chars:
        return cleaned
    return f"{cleaned[:max_chars]}..."


def elapsed_ms(started_at: float) -> float:
    return (time.monotonic() - started_at) * 1000


def log_startup_summary(config: dict[str, Any], log_file: Path) -> None:
    """Log high-signal startup context after logging has been configured."""
    bot_cfg = config.get("bot", {})
    napcat_cfg = config.get("napcat", {})
    paths = config.get("paths", {})
    features = config.get("features", {})

    logger.info(
        "[Startup] runtime pid=%s cwd=%s python=%s platform=%s",
        os.getpid(),
        Path.cwd(),
        sys.version.split()[0],
        platform.platform(),
    )
    logger.info(
        "[Startup] packages nonebot2=%s onebot_adapter=%s",
        _package_version("nonebot2"),
        _package_version("nonebot-adapter-onebot"),
    )
    logger.info(
        "[Startup] bot name=%s log_level=%s api_timeout=%s superuser=%s",
        bot_cfg.get("name", "HikariBotNeo"),
        bot_cfg.get("log_level", "INFO"),
        bot_cfg.get("api_timeout", 120),
        mask_identifier(bot_cfg.get("superuser_id", "")),
    )
    logger.info(
        "[Startup] napcat protocol=%s ws_url=%s token_configured=%s",
        napcat_cfg.get("protocol", "websocket"),
        redact_url(napcat_cfg.get("ws_url", "")),
        bool(napcat_cfg.get("token", "")),
    )
    logger.info(
        "[Startup] paths bot_data=%s user_data=%s logs=%s plugin_configs=%s temp_media=%s",
        paths.get("bot_data", "BotData"),
        paths.get("user_data", "UserData"),
        paths.get("logs", "BotData/logs"),
        paths.get("plugin_configs", "BotData/plugin_configs"),
        paths.get("temp_media", "/tmp/hikari_bot"),
    )
    logger.info("[Startup] log_file=%s", log_file)
    if features:
        logger.info("[Startup] features %s", _format_mapping(features))


def log_plugin_load_result(plugins: Iterable[Any], elapsed_seconds: float) -> None:
    names = sorted(_plugin_name(plugin) for plugin in plugins)
    logger.info(
        "[Startup] 插件目录加载完成 count=%d elapsed=%.2fs plugins=%s",
        len(names),
        elapsed_seconds,
        ", ".join(names) if names else "-",
    )


def register_driver_lifecycle_logs(driver: Any, startup_started_at: float) -> None:
    """Register NoneBot driver hooks that narrate the bot process lifecycle."""

    @driver.on_startup
    async def _log_driver_startup() -> None:
        logger.info(
            "[Lifecycle] Driver startup 完成 uptime=%.2fs adapters=%s loaded_plugins=%d bots=%d",
            time.monotonic() - startup_started_at,
            _driver_adapters(driver),
            _loaded_plugin_count(),
            _driver_bot_count(driver),
        )

    @driver.on_shutdown
    async def _log_driver_shutdown() -> None:
        logger.info(
            "[Lifecycle] Driver shutdown 开始 uptime=%.2fs bots=%d",
            time.monotonic() - startup_started_at,
            _driver_bot_count(driver),
        )

    if hasattr(driver, "on_bot_connect"):

        @driver.on_bot_connect
        async def _log_bot_connect(bot: BaseBot) -> None:
            logger.info(
                "[Lifecycle] Bot connected %s active_bots=%d",
                _describe_bot(bot),
                _driver_bot_count(driver),
            )

    if hasattr(driver, "on_bot_disconnect"):

        @driver.on_bot_disconnect
        async def _log_bot_disconnect(bot: BaseBot) -> None:
            logger.info(
                "[Lifecycle] Bot disconnected %s active_bots=%d",
                _describe_bot(bot),
                _driver_bot_count(driver),
            )


def _package_version(distribution_name: str) -> str:
    try:
        return version(distribution_name)
    except PackageNotFoundError:
        return "unknown"


def _format_mapping(values: dict[str, Any]) -> str:
    return " ".join(f"{key}={value}" for key, value in sorted(values.items()))


def _plugin_name(plugin: Any) -> str:
    return str(
        getattr(plugin, "name", None)
        or getattr(plugin, "module_name", None)
        or getattr(plugin, "__name__", None)
        or plugin
    )


def _driver_adapters(driver: Any) -> str:
    adapters = getattr(driver, "_adapters", None)
    if isinstance(adapters, dict):
        names = sorted(str(name) for name in adapters)
        return ",".join(names) if names else "-"
    return "-"


def _driver_bot_count(driver: Any) -> int:
    bots = getattr(driver, "bots", None)
    try:
        return len(bots) if bots is not None else 0
    except TypeError:
        return 0


def _loaded_plugin_count() -> int:
    try:
        from nonebot.plugin import get_loaded_plugins

        return len(get_loaded_plugins())
    except Exception:
        return 0


def _describe_bot(bot: Any) -> str:
    adapter = getattr(bot, "adapter", None)
    adapter_name = "-"
    if adapter is not None:
        get_name = getattr(adapter, "get_name", None)
        adapter_name = str(get_name() if callable(get_name) else adapter.__class__.__name__)
    return f"self_id={getattr(bot, 'self_id', '-')} adapter={adapter_name}"
