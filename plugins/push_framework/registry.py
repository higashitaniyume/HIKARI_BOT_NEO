from __future__ import annotations

import inspect
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Literal

from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment

logger = logging.getLogger("HikariBot.PushFramework.Registry")

PushTargetKind = Literal["group", "private"]


@dataclass(frozen=True, slots=True)
class PushTarget:
    kind: PushTargetKind
    target_id: int

    @property
    def label(self) -> str:
        prefix = "群" if self.kind == "group" else "私聊"
        return f"{prefix} {self.target_id}"


@dataclass(frozen=True, slots=True)
class PushContext:
    bot: Bot | None
    job_id: str
    source: str
    target: PushTarget
    options: dict[str, Any]
    now: datetime
    force: bool = False


@dataclass(frozen=True, slots=True)
class PushMessage:
    message: Any
    label: str = "推送消息"


PushSourceHandler = Callable[[PushContext], Awaitable[Any] | Any]


@dataclass(frozen=True, slots=True)
class PushSource:
    name: str
    handler: PushSourceHandler
    description: str = ""
    default_options: dict[str, Any] | None = None


_sources: dict[str, PushSource] = {}


def register_push_source(
    name: str,
    handler: PushSourceHandler | None = None,
    *,
    description: str = "",
    default_options: dict[str, Any] | None = None,
) -> Callable[[PushSourceHandler], PushSourceHandler] | PushSourceHandler:
    """Register a push message source.

    Source plugins can use this as a decorator or call it directly. The handler
    may be sync or async, and may return a string, Message, PushMessage, or a
    sequence of those values.
    """

    source_name = _normalize_source_name(name)

    def decorator(func: PushSourceHandler) -> PushSourceHandler:
        _sources[source_name] = PushSource(
            name=source_name,
            handler=func,
            description=description,
            default_options=dict(default_options or {}),
        )
        logger.info("已注册推送消息源: %s", source_name)
        return func

    if handler is None:
        return decorator
    return decorator(handler)


def get_push_source(name: str) -> PushSource | None:
    try:
        return _sources.get(_normalize_source_name(name))
    except ValueError:
        return None


def iter_push_sources() -> list[PushSource]:
    return list(_sources.values())


async def build_push_messages(source_name: str, context: PushContext) -> list[PushMessage]:
    source = get_push_source(source_name)
    if source is None:
        raise KeyError(f"未注册推送消息源: {source_name}")

    options = dict(source.default_options or {})
    options.update(context.options)
    context = PushContext(
        bot=context.bot,
        job_id=context.job_id,
        source=source.name,
        target=context.target,
        options=options,
        now=context.now,
        force=context.force,
    )

    result = source.handler(context)
    if inspect.isawaitable(result):
        result = await result
    return _normalize_push_result(result)


def _normalize_source_name(name: str) -> str:
    source_name = str(name or "").strip().casefold()
    if not source_name:
        raise ValueError("推送消息源名称不能为空")
    return source_name


def _normalize_push_result(value: Any) -> list[PushMessage]:
    if value is None:
        return []
    if isinstance(value, PushMessage):
        return [PushMessage(_coerce_message(value.message), value.label)]
    if isinstance(value, Message):
        return [PushMessage(value)]
    if isinstance(value, MessageSegment):
        return [PushMessage(Message(value))]
    if isinstance(value, str):
        text = value.strip()
        return [PushMessage(Message(text))] if text else []
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        messages: list[PushMessage] = []
        for item in value:
            messages.extend(_normalize_push_result(item))
        return messages
    return [PushMessage(Message(str(value)))]


def _coerce_message(value: Any) -> Message:
    if isinstance(value, Message):
        return value
    if isinstance(value, MessageSegment):
        return Message(value)
    return Message(str(value))
