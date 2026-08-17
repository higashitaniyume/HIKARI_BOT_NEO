"""AstrBot plugin dispatch — regex and on_message handler execution.

Separated from loader.py to keep loading logic separate from dispatch logic.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Any

from nonebot.adapters.onebot.v11 import Bot, MessageEvent

from astrbot.api.event import AstrMessageEvent
from astrbot.core.message.message_event_result import MessageEventResult

from plugins.astrbot_compat.conversion import convert_chain_to_onebot
from plugins.astrbot_compat.state import (
    PluginHandle,
    loaded_plugins as _loaded_plugins,
    on_message_handlers as _on_message_handlers,
    regex_matchers as _regex_matchers,
)

logger = logging.getLogger("AstrBotCompat.Dispatch")

_MAX_ONEBOT_MSG_BYTES = 900_000  # stay under WebSocket 1 MB limit


@dataclass(slots=True)
class DispatchResult:
    consumed: bool = False
    stopped: bool = False
    sent: bool = False
    result_set: bool = False
    exception: BaseException | None = None


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
        logger.debug(
            "on_message dispatch: plugin=[%s] text=%r",
            on_msg.plugin_name,
            text[:80],
        )
        result = await _run_handler(plugin, on_msg.handler, bot, event, text)
        handled = handled or result.consumed
        if result.stopped:
            break
    return handled


async def _run_handler(
    handle: PluginHandle,
    method: Any,
    bot: Bot,
    event: MessageEvent,
    text: str,
    **extra_kwargs: Any,
) -> DispatchResult:
    """Run a plugin handler (regex or on_message) bridging yield results."""
    from plugins.astrbot_compat.loader import _make_astr_event

    astr_event = _make_astr_event(bot, event, text)
    return await _run_generator(handle.instance, method, astr_event, bot, event, **extra_kwargs)


async def _run_generator(
    instance: Any,
    method: Any,
    astr_event: AstrMessageEvent,
    bot: Bot,
    event: MessageEvent,
    **extra_kwargs: Any,
) -> DispatchResult:
    """Consume an async generator handler and send results."""
    dispatch_result = DispatchResult()
    emitted_result_ids: set[int] = set()

    async def _event_send(message: Any) -> None:
        outbound = message
        if isinstance(message, MessageEventResult):
            outbound = convert_chain_to_onebot(message)
        elif not isinstance(message, str):
            outbound = convert_chain_to_onebot(message)
        if not outbound:
            return
        if await _safe_send(bot, event, outbound):
            dispatch_result.sent = True
            dispatch_result.consumed = True

    astr_event._set_send_hook(_event_send)

    async def _consume_result(result: Any) -> None:
        if isinstance(result, MessageEventResult):
            emitted_result_ids.add(id(result))
            if result.chain and await _send_result(bot, event, result):
                dispatch_result.sent = True
                dispatch_result.consumed = True
            if result.is_stopped():
                dispatch_result.stopped = True
                dispatch_result.consumed = True
        elif isinstance(result, str) and result:
            if await _safe_send(bot, event, result):
                dispatch_result.sent = True
                dispatch_result.consumed = True

    if extra_kwargs:
        gen = method(instance, astr_event, **extra_kwargs)
    else:
        gen = method(instance, astr_event)

    try:
        if inspect.isasyncgen(gen):
            async for result in gen:
                await _consume_result(result)
                if dispatch_result.stopped:
                    break
        else:
            # Regular coroutine that may return something
            result = await gen
            await _consume_result(result)
    except StopAsyncIteration:
        pass
    except Exception as e:
        dispatch_result.exception = e
        logger.exception(
            "Handler error: plugin=[%s] method=%s — %s",
            instance.__class__.__name__ if hasattr(instance, "__class__") else "?",
            method.__name__ if hasattr(method, "__name__") else "?",
            e,
        )
    finally:
        pending_result = astr_event.get_result()
        if pending_result is not None:
            dispatch_result.result_set = True
            dispatch_result.consumed = True
            if id(pending_result) not in emitted_result_ids:
                try:
                    await _consume_result(pending_result)
                except Exception as e:
                    dispatch_result.exception = dispatch_result.exception or e
                    logger.exception("Failed to send event.set_result() output: %s", e)
        if astr_event.is_stopped():
            dispatch_result.stopped = True
            dispatch_result.consumed = True
        astr_event._set_send_hook(None)

    return dispatch_result


async def _safe_send(bot: Bot, event: MessageEvent, message: Any) -> bool:
    """Send a message, gracefully handling oversized payloads."""
    try:
        await bot.send(event, message)
        return True
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
                return True
            except Exception:
                return False
        else:
            # Re-raise errors we don't know how to handle
            raise
    return False


async def _send_result(
    bot: Bot,
    event: MessageEvent,
    result: MessageEventResult,
) -> bool:
    """Convert a ``MessageEventResult`` to OneBot messages and send."""
    if not result.chain:
        return False

    ob_msg = convert_chain_to_onebot(result)
    if ob_msg:
        return await _safe_send(bot, event, ob_msg)
    return False
