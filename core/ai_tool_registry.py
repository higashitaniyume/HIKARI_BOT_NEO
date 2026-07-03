from __future__ import annotations

import inspect
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("HikariBot.AIToolRegistry")

_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True, slots=True)
class AIToolContext:
    bot: Any | None = None
    event: Any | None = None
    agent_config: dict[str, Any] | None = None


AIToolHandler = Callable[[AIToolContext, dict[str, Any]], Awaitable[Any] | Any]


@dataclass(frozen=True, slots=True)
class AIToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: AIToolHandler
    plugin_name: str = ""
    readonly: bool = True
    requires_superuser: bool = False
    enabled_by_default: bool = True

    def definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


_tools: dict[str, AIToolSpec] = {}


def register_ai_tool(
    name: str,
    *,
    description: str,
    parameters: dict[str, Any],
    plugin_name: str = "",
    readonly: bool = True,
    requires_superuser: bool = False,
    enabled_by_default: bool = True,
) -> Callable[[AIToolHandler], AIToolHandler]:
    """Register a plugin-owned function tool for the built-in AI agent."""

    tool_name = str(name or "").strip()
    if not _TOOL_NAME_RE.fullmatch(tool_name):
        raise ValueError(f"invalid AI tool name: {name!r}")
    if not isinstance(parameters, dict) or parameters.get("type") != "object":
        raise ValueError(f"AI tool {tool_name} parameters must be a JSON object schema")

    def decorator(func: AIToolHandler) -> AIToolHandler:
        _tools[tool_name] = AIToolSpec(
            name=tool_name,
            description=str(description or "").strip(),
            parameters=parameters,
            handler=func,
            plugin_name=str(plugin_name or "").strip(),
            readonly=bool(readonly),
            requires_superuser=bool(requires_superuser),
            enabled_by_default=bool(enabled_by_default),
        )
        logger.info("已注册 AI Tool: %s plugin=%s readonly=%s", tool_name, plugin_name or "-", bool(readonly))
        return func

    return decorator


def iter_ai_tools() -> list[AIToolSpec]:
    return list(_tools.values())


def get_ai_tool(name: str) -> AIToolSpec | None:
    return _tools.get(str(name or "").strip())


async def execute_ai_tool(name: str, context: AIToolContext | None, arguments: dict[str, Any]) -> str:
    spec = get_ai_tool(name)
    if spec is None:
        return _json_result({"error": f"unknown AI tool: {name}"})

    try:
        result = spec.handler(context or AIToolContext(), arguments)
        if inspect.isawaitable(result):
            result = await result
    except Exception as e:
        logger.warning("AI Tool 调用失败 tool=%s plugin=%s: %s", spec.name, spec.plugin_name or "-", e)
        return _json_result({"error": f"tool failed: {type(e).__name__}", "detail": str(e)[:300]})

    return _json_result(result)


def _json_result(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps({"result": value}, ensure_ascii=False)
    return json.dumps(_coerce_json_value(value), ensure_ascii=False)


def _coerce_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _coerce_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_coerce_json_value(item) for item in value]
    return str(value)
