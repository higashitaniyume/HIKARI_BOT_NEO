"""
AI Agent Wiki 工具预取模块。

负责 wiki 搜索工具的别名匹配、查询提取和自动预取逻辑。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from core.ai_tool_registry import AIToolContext

from .tools import execute_tool_call

logger = logging.getLogger("HikariBot.AIAgent.Wiki")

MC_WIKI_TOOL = "mc_wiki_search"
STARDEW_WIKI_TOOL = "stardew_wiki_search"
STS2_WIKI_TOOL = "sts2_wiki_search"
_WEB_SEARCH_TOOL = "web_search"
_MC_WIKI_ALIASES = (
    "mcwiki",
    "mc wiki",
    "minecraftwiki",
    "minecraft wiki",
    "minecraft百科",
    "minecraft维基",
    "我的世界wiki",
    "我的世界 wiki",
    "我的世界维基",
    "mc维基",
    "mc百科",
    "zh.minecraft.wiki",
)
_STARDEW_WIKI_ALIASES = (
    "星露谷wiki",
    "星露谷 wiki",
    "星露谷物语wiki",
    "星露谷物语 wiki",
    "星露谷维基",
    "星露谷物语维基",
    "svwiki",
    "sdvwiki",
    "stardewwiki",
    "stardew wiki",
    "stardew valley wiki",
    "zh.stardewvalleywiki.com",
)
_STS2_WIKI_ALIASES = (
    "塔2wiki",
    "塔2 wiki",
    "塔2维基",
    "塔2",
    "sts2wiki",
    "sts2 wiki",
    "sts2",
    "slay the spire 2 wiki",
    "slay the spire wiki",
    "slay the spire 2",
    "杀戮尖塔2wiki",
    "杀戮尖塔2 wiki",
    "杀戮尖塔 2 wiki",
    "杀戮尖塔2维基",
    "杀戮尖塔 2",
    "slaythespire.wiki.gg",
)
_LEADING_QUERY_FILLERS = (
    "里的",
    "里面的",
    "里面",
    "中的",
    "中",
    "关于",
    "查一下",
    "查询",
    "搜索",
    "查",
    "请问",
    "帮我",
    "一下",
)
_LEADING_PUNCT_RE = re.compile(r"^[\s:：,，.。;；!?！？\-_/\\|]+")


def _latest_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content") or "").strip()
    return ""


def _tool_names(tools: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for tool in tools:
        function = tool.get("function") if isinstance(tool, dict) else None
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "").strip()
        if name:
            names.add(name)
    return names


def _mentions_alias(text: str, aliases: tuple[str, ...]) -> bool:
    folded = text.casefold()
    compact = re.sub(r"\s+", "", folded)
    for alias in aliases:
        folded_alias = alias.casefold()
        if folded_alias in folded or re.sub(r"\s+", "", folded_alias) in compact:
            return True
    return False


def _wiki_priority_tool_names(text: str, names: set[str]) -> list[str]:
    result: list[str] = []
    if MC_WIKI_TOOL in names and _mentions_alias(text, _MC_WIKI_ALIASES):
        result.append(MC_WIKI_TOOL)
    if STARDEW_WIKI_TOOL in names and _mentions_alias(text, _STARDEW_WIKI_ALIASES):
        result.append(STARDEW_WIKI_TOOL)
    if STS2_WIKI_TOOL in names and _mentions_alias(text, _STS2_WIKI_ALIASES):
        result.append(STS2_WIKI_TOOL)
    return result


def _strip_query_fillers(value: str) -> str:
    query = _LEADING_PUNCT_RE.sub("", value.strip())
    changed = True
    while changed:
        changed = False
        for filler in _LEADING_QUERY_FILLERS:
            if query.startswith(filler):
                query = _LEADING_PUNCT_RE.sub("", query[len(filler):].strip())
                changed = True
                break
    return query.strip()


def _wiki_query_from_text(text: str, tool_name: str) -> str:
    aliases = _wiki_aliases(tool_name)
    folded = text.casefold()
    best_index = -1
    best_alias = ""
    for alias in sorted(aliases, key=len, reverse=True):
        index = folded.find(alias.casefold())
        if index >= 0 and (best_index < 0 or index < best_index):
            best_index = index
            best_alias = alias
    if best_index >= 0:
        query = _strip_query_fillers(text[best_index + len(best_alias):])
        if query:
            return query
    return text.strip()


def _wiki_aliases(tool_name: str) -> tuple[str, ...]:
    if tool_name == MC_WIKI_TOOL:
        return _MC_WIKI_ALIASES
    if tool_name == STARDEW_WIKI_TOOL:
        return _STARDEW_WIKI_ALIASES
    if tool_name == STS2_WIKI_TOOL:
        return _STS2_WIKI_ALIASES
    return ()


def _tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    import json

    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


async def _prefetch_wiki_priority_tools(
    cfg: dict[str, Any],
    request_messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    tool_context: AIToolContext | None,
) -> None:
    user_text = _latest_user_text(request_messages)
    if not user_text:
        return

    names = _tool_names(tools)
    wiki_tools = _wiki_priority_tool_names(user_text, names)
    if not wiki_tools:
        return

    calls: list[dict[str, Any]] = []
    for index, tool_name in enumerate(wiki_tools, start=1):
        calls.append(
            _tool_call(
                f"auto_{tool_name}_{index}",
                tool_name,
                {"query": _wiki_query_from_text(user_text, tool_name)},
            )
        )
    if _WEB_SEARCH_TOOL in names:
        calls.append(_tool_call("auto_web_search_after_wiki", _WEB_SEARCH_TOOL, {"query": user_text}))
    if not calls:
        return

    request_messages.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": calls,
        }
    )
    for tool_call in calls:
        request_messages.append(await execute_tool_call(cfg, tool_call, tool_context))
