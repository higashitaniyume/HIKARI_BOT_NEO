from __future__ import annotations

import json
import logging
from typing import Any

from . import files, search

logger = logging.getLogger("HikariBot.AIAgent.Tools")


def available_tools(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    if search.enabled(cfg):
        tools.append(search.definition())
    if files.enabled(cfg):
        tools.extend(files.definitions())
    return tools


async def execute_tool_call(cfg: dict[str, Any], tool_call: dict[str, Any]) -> dict[str, str]:
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
    else:
        content = json.dumps({"error": f"unknown or disabled tool: {name}"}, ensure_ascii=False)

    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "name": name,
        "content": content,
    }
