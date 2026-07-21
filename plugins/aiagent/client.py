from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from core.ai_tool_registry import AIToolContext
from core.bot_messages import get_message as msg

from .tools import available_tools, execute_tool_call
from .utils import parse_dsml_tool_calls, safe_float, safe_int, strip_dsml_tags

logger = logging.getLogger("HikariBot.AIAgent.Client")

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


_simple_chat_re = re.compile(
    r"^(?:\
        (?:你好|您好|你好呀|你好啊|嗨|hi|hello|hey|哈[喽罗]|在[吗嘛]|在不在)\
        |(?:谢谢|感谢|多谢|辛苦了|好的|好哒|好嘞|OK|ok|嗯|嗯嗯|行|可以)\
        |(?:再见|拜拜|白白|晚安|早安|早上好|下午好|晚上好|再见啦)\
        |(?:是[的哒]|不是|对|不对|没[事关系]|算[了吧]|好吧|就这样吧)\
        |(?:哈哈|哈哈哈|笑死|笑了|😊|😄|😂|🤣|👍|👌|❤️|💕)\
        |(?:明白|懂了|理解|知道了|收到|了解)\
        |(?:不知道|不会|不懂|不太清楚|没明白)\
    )$\
", re.IGNORECASE)


def _tool_wanted(text: str) -> bool:
    """Return True if the message plausibly needs external tools.

    Greetings, thanks, short affirmations, and emotional responses skip
    tool injection so the model replies naturally without hallucinating
    tool calls on simple chat.
    """
    stripped = text.strip()
    if not stripped or len(stripped) <= 2:
        return False
    if _simple_chat_re.match(stripped):
        return False
    return True


class AIAgentRequestError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"AI Agent 请求失败: HTTP {status_code} {detail}")
        self.status_code = status_code
        self.detail = detail


def _tools_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    return cfg.get("tools") if isinstance(cfg.get("tools"), dict) else {}


def endpoint(base_url: Any) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        base = "https://api.openai.com/v1"
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _assistant_tool_message(message: dict[str, Any]) -> dict[str, Any]:
    # DeepSeek V4 思考模式下 tool_calls 消息的 content 可能为 null，
    # 但后续请求要求 content 不可为空。转为空字符串以兼容。
    content = message.get("content")
    if content is None:
        content = ""
    result: dict[str, Any] = {
        "role": "assistant",
        "content": content,
        "tool_calls": message.get("tool_calls"),
    }
    # DeepSeek V4 思考模式下返回 reasoning_content，必须在多轮工具
    # 调用中保留，否则后续请求会出错。
    if "reasoning_content" in message:
        result["reasoning_content"] = message["reasoning_content"]
    return result


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


def _latest_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content") or "").strip()
    return ""


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


async def post_chat_completion(
    cfg: dict[str, Any],
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    model_cfg = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
    api_key = str(model_cfg.get("api_key") or "").strip()
    model = str(model_cfg.get("model") or "").strip()
    if not model:
        raise RuntimeError("AI Agent 模型名称未配置。")

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": safe_float(model_cfg.get("temperature"), 0.7, minimum=0.0, maximum=2.0),
        "top_p": safe_float(model_cfg.get("top_p"), 1.0, minimum=0.0, maximum=1.0),
        "max_tokens": safe_int(model_cfg.get("max_tokens"), 8192, minimum=1, maximum=131072),
    }
    tool_choice: str | None = model_cfg.get("tool_choice")
    if tools:
        payload["tools"] = tools
        # tool_choice 默认为 None（不传），以兼容 DeepSeek V4 思考模式。
        # 用户可在 config 中设为 "auto" / "none" / "required"。
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

    # DeepSeek V4 思考模式注入（thinking 配置段）
    thinking_cfg = cfg.get("thinking") if isinstance(cfg.get("thinking"), dict) else {}
    if thinking_cfg.get("enabled", True):
        payload["thinking"] = {"type": "enabled"}
        effort = thinking_cfg.get("reasoning_effort", "high")
        if effort in ("high", "max"):
            payload["reasoning_effort"] = effort
        if tool_choice is not None:
            logger.warning(
                "[AIAgent] 思考模式下 tool_choice 会被 API 忽略，"
                "建议在 config 中设置 \"tool_choice\": null"
            )
    else:
        payload["thinking"] = {"type": "disabled"}
    extra_body = model_cfg.get("extra_body")
    if isinstance(extra_body, dict):
        payload.update(extra_body)

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    timeout = httpx.Timeout(safe_int(model_cfg.get("timeout_seconds"), 60, minimum=5, maximum=600))
    proxy = str(model_cfg.get("proxy") or "").strip() or None
    async with httpx.AsyncClient(timeout=timeout, proxy=proxy) as client:
        response = await client.post(endpoint(model_cfg.get("base_url")), headers=headers, json=payload)
    if response.status_code >= 400:
        raise AIAgentRequestError(response.status_code, response.text[:400])

    data = response.json()
    choices = data.get("choices") if isinstance(data, dict) else None
    if not choices:
        raise RuntimeError("AI Agent 返回结果为空。")
    message = choices[0].get("message") if isinstance(choices[0], dict) else {}
    if not isinstance(message, dict):
        raise RuntimeError("AI Agent 返回消息格式无效。")
    return message


async def request_chat_completion(
    cfg: dict[str, Any],
    messages: list[dict[str, str]],
    tool_context: AIToolContext | None = None,
) -> str:
    plain_request_messages: list[dict[str, Any]] = [dict(message) for message in messages]
    request_messages: list[dict[str, Any]] = [dict(message) for message in messages]
    all_tools = available_tools(cfg, tool_context)
    user_text = _latest_user_text(request_messages)
    tools = all_tools if _tool_wanted(user_text) else []
    max_rounds = safe_int(_tools_cfg(cfg).get("max_tool_rounds"), 4, minimum=0, maximum=10)
    if tools and max_rounds > 0:
        await _prefetch_wiki_priority_tools(cfg, request_messages, tools, tool_context)

    for round_index in range(max_rounds + 1):
        try:
            message = await post_chat_completion(cfg, request_messages, tools)
        except AIAgentRequestError as e:
            if tools and e.status_code in {400, 422}:
                logger.warning("[AIAgent] 当前模型接口可能不支持 tools，已降级为普通聊天: %s", e)
                tools = []
                request_messages = [dict(message) for message in plain_request_messages]
                message = await post_chat_completion(cfg, request_messages, tools)
            else:
                raise
        tool_calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
        content = str(message.get("content") or "").strip()
        # DeepSeek V4 Flash DSML 降级: 思考模式下 API 可能将工具调用以 DSML
        # 标签形式嵌入 content 而非标准 tool_calls 字段，需手动解析。
        if not tool_calls and content:
            dsml_calls = parse_dsml_tool_calls(content)
            if dsml_calls:
                logger.debug(
                    "[AIAgent] 从 DSML 内容解析到 %d 个工具调用",
                    len(dsml_calls),
                )
                cleaned_content = strip_dsml_tags(content)
                message["content"] = cleaned_content or ""
                message["tool_calls"] = dsml_calls
                tool_calls = dsml_calls
        if not tool_calls:
            if not content:
                raise RuntimeError("AI Agent 回复为空。")
            return content

        if round_index >= max_rounds:
            # Tool rounds exhausted — drop tools and ask the model to
            # synthesise a final answer from whatever it already gathered.
            if content:
                return content
            logger.warning(
                "[AIAgent] 工具调用轮数已达上限，移除 tools 强制生成回复 tools=%s",
                ", ".join(
                    str(tc.get("function", {}).get("name", "?"))
                    for tc in tool_calls if isinstance(tc, dict)
                ) or "?",
            )
            tools = []
            request_messages.append(_assistant_tool_message(message))
            for tool_call in tool_calls:
                if isinstance(tool_call, dict):
                    request_messages.append(await execute_tool_call(cfg, tool_call, tool_context))
            message = await post_chat_completion(cfg, request_messages, tools)
            content = str(message.get("content") or "").strip()
            return content or msg("aiagent.tool_limit_reached")

        request_messages.append(_assistant_tool_message(message))
        for tool_call in tool_calls:
            if isinstance(tool_call, dict):
                request_messages.append(await execute_tool_call(cfg, tool_call, tool_context))

    raise RuntimeError("AI Agent 回复为空。")
