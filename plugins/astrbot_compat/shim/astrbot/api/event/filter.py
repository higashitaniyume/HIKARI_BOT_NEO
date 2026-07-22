"""AstrBot filter decorators shim — command, regex, on_message."""

from __future__ import annotations

import inspect
import re
from typing import Any, Callable

from nonebot.adapters.onebot.v11 import Bot, MessageEvent

from astrbot.api.event import AstrMessageEvent

# Decorator metadata keys
_ATTR_COMMAND = "_astrbot_command"
_ATTR_ALIASES = "_astrbot_aliases"
_ATTR_COMMAND_GROUP = "_astrbot_command_group"
_ATTR_PARAM_INFO = "_astrbot_param_info"
_ATTR_REGEX = "_astrbot_regex"
_ATTR_ON_MESSAGE = "_astrbot_on_message"
_ATTR_PERMISSION = "_astrbot_permission"
_ATTR_EVENT_TYPE = "_astrbot_event_type"

# Sentinel for greedily consuming all remaining args
_GREEDY_SENTINEL = object()


# ---------------------------------------------------------------------------
# Public decorators
# ---------------------------------------------------------------------------


def command(
    name: str,
    alias: set[str] | None = None,
    **kwargs,
) -> Callable[[Callable], Callable]:
    """Register a command handler with optional parameter auto-resolution.

    Inspired by AstrBot's ``CommandFilter`` which inspects the handler
    signature and extracts typed arguments from the raw message string.

    Supported parameter annotations for auto-resolution:
        str, int, float, bool, Optional[T], ``GreedyStr`` (all remaining text).

    Usage::

        @filter.command("add")
        async def add(self, event, a: int, b: int):
            yield event.plain_result(f"{a} + {b} = {a + b}")

        @filter.command("echo", alias={"say"})
        async def echo(self, event, *, message: GreedyStr):
            yield event.plain_result(message)
    """
    def decorator(func: Callable) -> Callable:
        setattr(func, _ATTR_COMMAND, name)
        setattr(func, _ATTR_ALIASES, set(alias or ()))

        # Introspect signature & store param info for the loader
        sig = inspect.signature(func)
        params = list(sig.parameters.values())
        # Skip self, event → consumer params start at index 2
        consumer = params[2:]
        param_info: list[dict[str, Any]] = []
        for p in consumer:
            annotation = p.annotation if p.annotation is not inspect.Parameter.empty else str
            has_default = p.default is not inspect.Parameter.empty
            default_val = p.default if has_default else None

            # Resolve GreedyStr marker
            is_greedy = _is_greedy(annotation)

            # Resolve Optional[T] → get the inner type
            resolved = _unwrap_optional(annotation)
            native_type = resolved if isinstance(resolved, type) else str

            param_info.append({
                "name": p.name,
                "annotation": native_type,
                "has_default": has_default,
                "default": default_val,
                "is_greedy": is_greedy,
                "kind": p.kind,  # POSITIONAL_OR_KEYWORD, KEYWORD_ONLY
            })

        if param_info:
            setattr(func, _ATTR_PARAM_INFO, param_info)
        return func
    return decorator


def command_group(name: str, **kwargs) -> Callable[[Callable], Callable]:
    """Register a sub-command group.

    Usage::

        @filter.command_group("admin")
        class AdminCommands:
            @filter.command("ban")
            async def ban(self, event, user: str):
                ...
    """
    def decorator(func_or_cls: Callable) -> Callable:
        setattr(func_or_cls, _ATTR_COMMAND_GROUP, name)
        return func_or_cls
    return decorator


def regex(pattern: str, **kwargs) -> Callable[[Callable], Callable]:
    """Register a regex-based message handler.

    Matched groups are injected into the method as keyword arguments::

        @filter.regex(r"hello\s+(?P<name>\w+)")
        async def on_hello(self, event, name: str):
            yield event.plain_result(f"Hello, {name}!")
    """
    def decorator(func: Callable) -> Callable:
        setattr(func, _ATTR_REGEX, re.compile(pattern))
        return func
    return decorator


def on_message(**kwargs) -> Callable[[Callable], Callable]:
    """Register a catch-all message handler."""
    def decorator(func: Callable) -> Callable:
        setattr(func, _ATTR_ON_MESSAGE, True)
        return func
    return decorator


def permission(type: str = "all", **kwargs) -> Callable[[Callable], Callable]:
    """Restrict command handler to a permission scope.

    Supported types:
        ``"all"`` — any user
        ``"admin"`` — bot superuser / group admin
        ``"superuser"`` — bot superuser only

    Usage::

        @filter.command("admin-only-cmd")
        @filter.permission("admin")
        async def secret(self, event):
            ...
    """
    def decorator(func: Callable) -> Callable:
        setattr(func, _ATTR_PERMISSION, type)
        return func
    return decorator


def event_message_type(type: str = "all", **kwargs) -> Callable[[Callable], Callable]:
    """Filter handler by message type.

    Supported types: ``"all"``, ``"group"``, ``"private"``.

    Usage::

        @filter.command("group-only")
        @filter.event_message_type("group")
        async def group_cmd(self, event):
            ...
    """
    def decorator(func: Callable) -> Callable:
        setattr(func, _ATTR_EVENT_TYPE, type)
        return func
    return decorator


# ---------------------------------------------------------------------------
# GreedyStr marker
# ---------------------------------------------------------------------------


class GreedyStr(str):
    """Marker type that consumes all remaining positional arguments as one string."""
    pass


# ---------------------------------------------------------------------------
# Inspector helpers for the loader
# ---------------------------------------------------------------------------


def get_command_meta(func: Callable) -> dict[str, Any] | None:
    name = getattr(func, _ATTR_COMMAND, None)
    if name is None:
        return None
    return {
        "name": name,
        "alias": getattr(func, _ATTR_ALIASES, set()) or set(),
        "params": getattr(func, _ATTR_PARAM_INFO, []),
    }


def get_regex_meta(func: Callable) -> re.Pattern | None:
    return getattr(func, _ATTR_REGEX, None)


def is_on_message(func: Callable) -> bool:
    return getattr(func, _ATTR_ON_MESSAGE, False) or False


def get_permission_meta(func: Callable) -> str | None:
    return getattr(func, _ATTR_PERMISSION, None)


def get_event_type_meta(func: Callable) -> str | None:
    return getattr(func, _ATTR_EVENT_TYPE, None)


# ---------------------------------------------------------------------------
# Argument parsing utility (used by the loader)
# ---------------------------------------------------------------------------


def parse_command_args(
    raw_args: str,
    param_info: list[dict[str, Any]],
) -> dict[str, Any]:
    """Parse a raw argument string according to parameter metadata.

    Returns a dict of ``{param_name: value}`` suitable for ``**kwargs``
    injection into the handler.
    """
    if not param_info:
        return {}

    result: dict[str, Any] = {}
    tokens = raw_args.split() if raw_args.strip() else []

    token_index = 0
    for info in param_info:
        if info["is_greedy"]:
            # Consume all remaining tokens
            result[info["name"]] = " ".join(tokens[token_index:]) if token_index < len(tokens) else (
                info["default"] if info["has_default"] else ""
            )
            token_index = len(tokens)
            break

        if info["kind"] == inspect.Parameter.KEYWORD_ONLY:
            result[info["name"]] = " ".join(tokens[token_index:]) if token_index < len(tokens) else (
                info["default"] if info["has_default"] else ""
            )
            token_index = len(tokens)
            break

        if token_index >= len(tokens):
            if info["has_default"]:
                result[info["name"]] = info["default"]
                continue
            result[info["name"]] = _coerce("", info["annotation"])
            continue

        raw = tokens[token_index]
        result[info["name"]] = _coerce(raw, info["annotation"])
        token_index += 1

    return result


def _coerce(raw: str, target: type) -> Any:
    """Coerce a string token to the target type."""
    if target is str or target is GreedyStr:
        return raw
    if target is int:
        try:
            return int(raw)
        except (ValueError, TypeError):
            return 0
    if target is float:
        try:
            return float(raw)
        except (ValueError, TypeError):
            return 0.0
    if target is bool:
        return raw.lower() in ("true", "1", "yes", "y")
    return raw


def _unwrap_optional(annotation: Any) -> Any:
    """Strip ``Optional[X]``, ``Union[X, None]``, ``X | None`` down to X."""
    origin = getattr(annotation, "__origin__", None)
    if origin is not None:
        args = getattr(annotation, "__args__", [])
        non_none = [a for a in args if a is not type(None)]
        if non_none:
            return non_none[0]
    return annotation


def _is_greedy(annotation: Any) -> bool:
    """Check if the annotation is/contains ``GreedyStr``."""
    if annotation is GreedyStr:
        return True
    if _unwrap_optional(annotation) is GreedyStr:
        return True
    return False
