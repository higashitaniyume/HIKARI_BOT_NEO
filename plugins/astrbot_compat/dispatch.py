"""AstrBot plugin dispatch — regex and on_message handler execution.

Separated from loader.py to keep loading logic separate from dispatch logic.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from nonebot.adapters.onebot.v11 import Bot, MessageEvent

from astrbot.api.event import AstrMessageEvent
from astrbot.core.message.message_event_result import MessageEventResult

from plugins.astrbot_compat.conversion import convert_chain_to_onebot
from plugins.astrbot_compat.loader import (
    PluginHandle,
    _loaded_plugins,
    _on_message_handlers,
    _regex_matchers,
)

logger = logging.getLogger("AstrBotCompat.Dispatch")

_MAX_ONEBOT_MSG_BYTES = 900_000  # stay under WebSocket 1 MB limit


async def dispatch_regex_command(
    bot: Bot,
    event: MessageEvent,
    text: str,
) -> bool:
    """Dispatch a message to all loaded regex handlers. Return True if matched."""
    matched = False
    for regex_matcher in _regex_matchers:
        m = regex_matcher.pattern.search(text)
        if m:
            plugin = _loaded_plugins.get(regex_matcher.plugin_name)
            if plugin is None:
                logger.debug("Regex match but plugin %s is gone", regex_matcher.plugin_name)
                continue
            matched = True
            logger.debug(
                "Regex matched: plugin=[%s] pattern=%s text=%r groups=%s",
                regex_matcher.plugin_name,
                regex_matcher.pattern.pattern,
                text[:80],
                m.groupdict(),
            )
            await _run_handler(
                plugin,
                regex_matcher.handler,
                bot,
                event,
                text,
                **m.groupdict(),
            )
    return matched


async def dispatch_on_message(
    bot: Bot,
    event: MessageEvent,
    text: str,
) -> bool:
    """Dispatch a message to all loaded catch-all handlers. Return True if any handled."""
    handled = False
    for on_msg in _on_message_handlers:
        plugin = _loaded_plugins.get(on_msg.plugin_name)
        if plugin is None:
            continue
        handled = True
        logger.debug(
            "on_message dispatch: plugin=[%s] text=%r",
            on_msg.plugin_name,
            text[:80],
        )
        await _run_handler(plugin, on_msg.handler, bot, event, text)
    return handled


async def _run_handler(
    handle: PluginHandle,
    method: Any,
    bot: Bot,
    event: MessageEvent,
    text: str,
    **extra_kwargs: Any,
) -> None:
    """Run a plugin handler (regex or on_message) bridging yield results."""
    from plugins.astrbot_compat.loader import _make_astr_event

    astr_event = _make_astr_event(bot, event, text)
    await _run_generator(handle.instance, method, astr_event, bot, event, **extra_kwargs)


async def _run_generator(
    instance: Any,
    method: Any,
    astr_event: AstrMessageEvent,
    bot: Bot,
    event: MessageEvent,
    **extra_kwargs: Any,
) -> None:
    """Consume an async generator handler and send results."""
    if extra_kwargs:
        gen = method(instance, astr_event, **extra_kwargs)
    else:
        gen = method(instance, astr_event)

    try:
        if inspect.isasyncgen(gen):
            async for result in gen:
                if isinstance(result, MessageEventResult):
                    await _send_result(bot, event, result)
                    if result.is_stopped():
                        break
                elif isinstance(result, str):
                    await _safe_send(bot, event, result)
        else:
            # Regular coroutine that may return something
            result = await gen
            if isinstance(result, MessageEventResult):
                await _send_result(bot, event, result)
            elif isinstance(result, str):
                await _safe_send(bot, event, result)
    except StopAsyncIteration:
        pass
    except Exception as e:
        logger.exception(
            "Handler error: plugin=[%s] method=%s — %s",
            instance.__class__.__name__ if hasattr(instance, "__class__") else "?",
            method.__name__ if hasattr(method, "__name__") else "?",
            e,
        )


async def _safe_send(bot: Bot, event: MessageEvent, message: Any) -> None:
    """Send a message, gracefully handling oversized payloads."""
    try:
        await bot.send(event, message)
    except Exception as e:
        err_str = str(e)
        # Catch WebSocket message-too-big errors and similar
        if "too big" in err_str or "exceeds limit" in err_str or "1009" in err_str:
            logger.warning(
                "Message too large to send (%s) — notifying user instead",
                err_str[:120],
            )
            try:
                await bot.send(
                    event,
                    "⚠️ 插件返回的消息过大（超过 1 MB），"
                    "NapCat WebSocket 无法传输。请联系管理员。",
                )
            except Exception:
                pass
        else:
            # Re-raise errors we don't know how to handle
            raise


async def _send_result(
    bot: Bot,
    event: MessageEvent,
    result: MessageEventResult,
) -> None:
    """Convert a ``MessageEventResult`` to OneBot messages and send."""
    if not result.chain:
        return

    ob_msg = convert_chain_to_onebot(result)
    if ob_msg:
        await _safe_send(bot, event, ob_msg)
