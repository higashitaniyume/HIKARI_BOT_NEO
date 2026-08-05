from __future__ import annotations

import json
import logging
from typing import Any

from core.ai_tool_registry import AIToolContext, execute_ai_tool, iter_ai_tools
from core.config_loader import load_main_config

from ..config import api_protocol
from ..utils import safe_bool

from . import files, help, search

logger = logging.getLogger("HikariBot.AIAgent.Tools")


def _use_builtin_search(cfg: dict[str, Any]) -> bool:
    """Responses 协议 + mode=builtin 时使用服务端内置 web_search 工具。"""
    return api_protocol(cfg) == "responses" and search.mode(cfg) == "builtin"


def available_tools(cfg: dict[str, Any], context: AIToolContext | None = None) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    if search.enabled(cfg):
        if _use_builtin_search(cfg):
            # DeepSeek Responses API 服务端内置搜索：模型发出 web_search_call，
            # 服务端执行搜索并自动注入结果，无需本地函数工具。
            tools.append(search.builtin_definition())
        else:
            tools.append(search.definition())
    if files.enabled(cfg):
        tools.extend(files.definitions())
    if help.enabled(cfg):
        tools.append(help.definition())
    if _plugin_tools_enabled(cfg):
        tools.extend(spec.definition() for spec in _iter_enabled_plugin_tools(cfg, context))
    return tools


async def execute_tool_call(
    cfg: dict[str, Any],
    tool_call: dict[str, Any],
    context: AIToolContext | None = None,
) -> dict[str, str]:
    tool_call_id = str(tool_call.get("id") or "")
    function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
    name = str(function.get("name") or "").strip()
    raw_arguments = str(function.get("arguments") or "{}")
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError:
        arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}

    if name == search.TOOL_NAME and search.enabled(cfg):
        if _use_builtin_search(cfg):
            # 内置搜索由服务端执行，不应出现函数调用；防御模型误调用
            content = json.dumps(
                {"error": "web_search 由服务端内置执行，无需函数调用"},
                ensure_ascii=False,
            )
        else:
            try:
                content = await search.execute(cfg, arguments)
            except Exception as e:
                logger.warning("[AIAgent] 搜索工具调用失败: %s", e)
                content = json.dumps({"error": f"search failed: {e}"}, ensure_ascii=False)
    elif files.can_handle(name) and files.enabled(cfg):
        try:
            content = files.execute(name, cfg, arguments)
        except Exception as e:
            logger.warning("[AIAgent] 文件工具调用失败: %s", e)
            content = json.dumps({"error": f"file tool failed: {e}"}, ensure_ascii=False)
    elif name == help.TOOL_NAME and help.enabled(cfg):
        try:
            content = help.execute(cfg, arguments)
        except Exception as e:
            logger.warning("[AIAgent] 帮助文档工具调用失败: %s", e)
            content = json.dumps({"error": f"help tool failed: {e}"}, ensure_ascii=False)
    elif _plugin_tools_enabled(cfg) and _plugin_tool_allowed(name, cfg, context):
        content = await execute_ai_tool(name, context, arguments)
    else:
        content = json.dumps({"error": f"unknown or disabled tool: {name}"}, ensure_ascii=False)

    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "name": name,
        "content": content,
    }


def _tools_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    return cfg.get("tools") if isinstance(cfg.get("tools"), dict) else {}


def _plugin_tools_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    tools_cfg = _tools_cfg(cfg)
    return tools_cfg.get("plugin_tools") if isinstance(tools_cfg.get("plugin_tools"), dict) else {}


def _plugin_tools_enabled(cfg: dict[str, Any]) -> bool:
    plugin_cfg = _plugin_tools_cfg(cfg)
    return bool(plugin_cfg) and safe_bool(plugin_cfg.get("enabled"), True)


def _configured_names(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}


def _iter_enabled_plugin_tools(cfg: dict[str, Any], context: AIToolContext | None):
    plugin_cfg = _plugin_tools_cfg(cfg)
    enabled_names = _configured_names(plugin_cfg.get("enabled_names"))
    disabled_names = _configured_names(plugin_cfg.get("disabled_names"))
    allow_side_effects = safe_bool(plugin_cfg.get("allow_side_effects"), False)

    for spec in iter_ai_tools():
        if not spec.enabled_by_default and spec.name not in enabled_names:
            continue
        if enabled_names and spec.name not in enabled_names:
            continue
        if spec.name in disabled_names:
            continue
        if not spec.readonly and not allow_side_effects:
            continue
        if spec.requires_superuser and not _is_superuser(context):
            continue
        yield spec


def _plugin_tool_allowed(name: str, cfg: dict[str, Any], context: AIToolContext | None) -> bool:
    return any(spec.name == name for spec in _iter_enabled_plugin_tools(cfg, context))


def _is_superuser(context: AIToolContext | None) -> bool:
    if context is None or context.event is None:
        return False
    try:
        superuser_id = str(load_main_config().get("bot", {}).get("superuser_id") or "").strip()
        return bool(superuser_id) and str(context.event.get_user_id()).strip() == superuser_id
    except Exception:
        return False
