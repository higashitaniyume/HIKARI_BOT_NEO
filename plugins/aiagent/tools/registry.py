from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from core.ai_tool_registry import AIToolContext, execute_ai_tool, iter_ai_tools
from core.config_loader import load_main_config

from ..utils import safe_bool, safe_float

from . import files, group, group_actions, help, images, search, workspace

logger = logging.getLogger("HikariBot.AIAgent.Tools")

# 单个工具调用的默认超时（秒）。挂住的工具会拖住整轮回复，也会占住该会话的锁。
DEFAULT_TOOL_TIMEOUT_SECONDS = 30.0


def _tool_timeout(cfg: dict[str, Any]) -> float:
    return safe_float(
        _tools_cfg(cfg).get("tool_timeout_seconds"),
        DEFAULT_TOOL_TIMEOUT_SECONDS,
        minimum=0.1,
        maximum=600.0,
    )


async def _run_tool(coro: Any, name: str, timeout: float) -> str:
    """执行工具调用并施加单次超时；超时按工具错误返回，不让整轮回复失败。"""
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("[AIAgent] 工具调用超时 %.1fs: %s", timeout, name)
        return json.dumps(
            {"error": f"tool {name} timed out after {timeout:g}s"}, ensure_ascii=False
        )


def _tool_name(tool: dict[str, Any]) -> str:
    function = tool.get("function") if isinstance(tool, dict) else None
    return str(function.get("name") or "") if isinstance(function, dict) else ""


def available_tools(cfg: dict[str, Any], context: AIToolContext | None = None) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    if search.enabled(cfg):
        # DeepSeek 官方 Search API 与 SearXNG 都走同一个 web_search 函数工具，
        # 参数与描述按当前后端区分（官方搜索支持 site:/lang:/after: 等语法）。
        tools.append(search.definition(cfg))
    if images.enabled(cfg):
        tools.extend(images.definitions(cfg))
    if workspace.enabled(cfg):
        tools.extend(workspace.definitions(cfg))
    if files.enabled(cfg):
        tools.extend(files.definitions(cfg))
    if group.is_group_event(context):
        # 群聊工具只在群聊里下发：私聊既没有群可查，也不该出现这些能力
        tools.extend(group.definitions(cfg))
        # 群管理 / 互动工具（禁言、踢人、戳一戳、@）另有一层权限与主动接话校验
        tools.extend(group_actions.definitions(cfg, context))
    if help.enabled(cfg):
        tools.append(help.definition())
    if _plugin_tools_enabled(cfg):
        tools.extend(spec.definition() for spec in _iter_enabled_plugin_tools(cfg, context))
    # 工具定义排在请求最前面，顺序一变整段前缀缓存就作废；按名字排序保证
    # 重启、插件加载顺序变化都不会改变工具块的字节。
    tools.sort(key=_tool_name)
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

    timeout = _tool_timeout(cfg)

    if name == search.TOOL_NAME and search.enabled(cfg):
        try:
            content = await _run_tool(search.execute(cfg, arguments, context), name, timeout)
        except Exception as e:
            logger.warning("[AIAgent] 搜索工具调用失败: %s", e)
            content = json.dumps({"error": f"search failed: {e}"}, ensure_ascii=False)
    elif images.can_handle(name) and images.enabled(cfg):
        try:
            content = await _run_tool(images.execute(name, cfg, arguments, context), name, timeout)
        except Exception as e:
            logger.warning("[AIAgent] 图片工具调用失败: %s", e)
            content = json.dumps({"error": f"images tool failed: {e}"}, ensure_ascii=False)
    elif workspace.can_handle(name) and workspace.enabled(cfg):
        try:
            content = await _run_tool(workspace.execute(name, cfg, arguments, context), name, timeout)
        except Exception as e:
            logger.warning("[AIAgent] 工作区工具调用失败: %s", e)
            content = json.dumps({"error": f"workspace tool failed: {e}"}, ensure_ascii=False)
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
    elif group.can_handle(name) and group.enabled(cfg, name) and group.is_group_event(context):
        try:
            content = await _run_tool(group.execute(name, cfg, arguments, context), name, timeout)
        except Exception as e:
            logger.warning("[AIAgent] 群聊工具调用失败: %s", e)
            content = json.dumps({"error": f"group tool failed: {e}"}, ensure_ascii=False)
    elif (
        group_actions.can_handle(name)
        and group_actions.enabled(cfg, name)
        and group_actions.allowed_in_context(context)
    ):
        try:
            content = await _run_tool(
                group_actions.execute(name, cfg, arguments, context), name, timeout
            )
        except Exception as e:
            logger.warning("[AIAgent] 群管理工具调用失败: %s", e)
            content = json.dumps({"error": f"group action tool failed: {e}"}, ensure_ascii=False)
    elif _plugin_tools_enabled(cfg) and _plugin_tool_allowed(name, cfg, context):
        try:
            content = await _run_tool(execute_ai_tool(name, context, arguments), name, timeout)
        except Exception as e:
            logger.warning("[AIAgent] 插件工具调用失败: %s", e)
            content = json.dumps({"error": f"plugin tool failed: {e}"}, ensure_ascii=False)
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
