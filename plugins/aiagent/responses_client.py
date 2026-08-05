"""DeepSeek Responses API 客户端。

对接 DeepSeek 新架构（https://api-docs.deepseek.com/zh-cn/guides/responses_api）：
- 无状态 Responses API，兼容 OpenAI Responses API 格式，不支持 previous_response_id；
- 多轮工具调用通过 input items 数组构建（message / function_call /
  function_call_output / web_search_call）；
- web_search 为服务端内置工具（{"type": "web_search"}），模型发出 web_search_call
  后由服务端执行搜索；把 web_search_call 原样回传即可恢复搜索结果；
- 思考模式通过 reasoning.effort 控制。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from core.ai_tool_registry import AIToolContext
from core.bot_messages import get_message as msg

from .client import AIAgentRequestError, _tool_wanted, _tools_cfg
from .tools import available_tools, execute_tool_call
from .utils import safe_float, safe_int
from .wiki import _latest_user_text, _prefetch_wiki_priority_items

logger = logging.getLogger("HikariBot.AIAgent.Responses")

_REASONING_EFFORTS = {"low", "medium", "high", "max"}


def endpoint(base_url: Any) -> str:
    """Responses API 端点。

    兼容 https://api.deepseek.com 与 https://api.deepseek.com/v1 两种 base_url 写法
    （后者是 Chat Completions 风格的地址，剥掉 /v1 后拼 /responses）。
    """
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        base = "https://api.deepseek.com"
    if base.endswith("/v1"):
        base = base[:-3]
    if base.endswith("/responses"):
        return base
    return f"{base}/responses"


def _message_item(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": message.get("role") or "user",
        "content": str(message.get("content") or ""),
    }


def _split_instructions(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """把聊天格式消息拆成 instructions（首条 system）+ input items。

    首条 system（persona + 固定指令）作为独立的 instructions 字段发送，
    保持前缀稳定以命中 DeepSeek 自动前缀缓存；其余消息转成 input items。
    """
    instructions = ""
    rest = messages
    if messages and messages[0].get("role") == "system":
        instructions = str(messages[0].get("content") or "").strip()
        rest = messages[1:]
    return instructions, [_message_item(message) for message in rest]


def _build_payload(
    cfg: dict[str, Any],
    instructions: str,
    input_items: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    model_cfg = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
    model = str(model_cfg.get("model") or "").strip()
    if not model:
        raise RuntimeError("AI Agent 模型名称未配置。")

    payload: dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "input": input_items,
        "temperature": safe_float(model_cfg.get("temperature"), 0.7, minimum=0.0, maximum=2.0),
        "top_p": safe_float(model_cfg.get("top_p"), 1.0, minimum=0.0, maximum=1.0),
        # Responses API 使用 max_output_tokens（复用配置中的 max_tokens）
        "max_output_tokens": safe_int(model_cfg.get("max_tokens"), 8192, minimum=1, maximum=131072),
    }
    tool_choice = model_cfg.get("tool_choice")
    if tools:
        payload["tools"] = tools
        # 默认不传 tool_choice（auto）：内置 web_search 由模型按需触发。
        # 用户可在 config 中设为 "auto" / "none" / "required" 或 {"type": "web_search"}。
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

    # 思考模式：Responses API 用 reasoning.effort（而非 Chat Completions 的
    # thinking 字段）。关闭时直接不传 reasoning，由模型自身默认行为决定。
    thinking_cfg = cfg.get("thinking") if isinstance(cfg.get("thinking"), dict) else {}
    if thinking_cfg.get("enabled", True):
        effort = str(thinking_cfg.get("reasoning_effort") or "high").strip().lower()
        if effort not in _REASONING_EFFORTS:
            effort = "high"
        payload["reasoning"] = {"effort": effort}

    extra_body = model_cfg.get("extra_body")
    if isinstance(extra_body, dict):
        payload.update(extra_body)
    return payload


async def post_response(
    cfg: dict[str, Any],
    instructions: str,
    input_items: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    """发送一次 Responses API 请求，返回完整 response 对象。"""
    model_cfg = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
    api_key = str(model_cfg.get("api_key") or "").strip()
    payload = _build_payload(cfg, instructions, input_items, tools)

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
    if not isinstance(data, dict) or not isinstance(data.get("output"), list):
        raise RuntimeError("AI Agent 返回格式无效。")
    return data


def _output_items(response: dict[str, Any]) -> list[dict[str, Any]]:
    output = response.get("output")
    return [item for item in output if isinstance(item, dict)]


def _function_call_items(response: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in _output_items(response) if item.get("type") == "function_call"]


def _passthrough_items(response: dict[str, Any]) -> list[dict[str, Any]]:
    """需要回传给服务端的 output item：function_call 与 web_search_call。

    web_search_call 原样回传后服务端会自动恢复搜索结果（无状态 API 的
    多轮跟进方式）；function_call 连同 function_call_output 一起回传。
    """
    return [
        item for item in _output_items(response) if item.get("type") in {"function_call", "web_search_call"}
    ]


def _output_text(response: dict[str, Any]) -> str:
    """提取最终回复文本：优先 output_text 字段，兜底从最后一条 message item 提取。"""
    text = str(response.get("output_text") or "").strip()
    if text:
        return text
    for item in reversed(_output_items(response)):
        if item.get("type") != "message":
            continue
        parts: list[str] = []
        content = item.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                    parts.append(str(part.get("text") or ""))
        elif isinstance(content, str):
            parts.append(content)
        return "".join(parts).strip()
    return ""


async def _function_call_output_item(
    cfg: dict[str, Any],
    call: dict[str, Any],
    tool_context: AIToolContext | None,
) -> dict[str, Any]:
    """执行一个 function_call item，转成 function_call_output input item。"""
    call_id = str(call.get("call_id") or call.get("id") or "")
    name = str(call.get("name") or "").strip()
    arguments = str(call.get("arguments") or "{}")
    # 转成 registry 期望的 OpenAI Chat 格式再执行，复用既有工具分发逻辑
    result = await execute_tool_call(
        cfg,
        {"id": call_id, "function": {"name": name, "arguments": arguments}},
        tool_context,
    )
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": str(result.get("content") or ""),
    }


async def request_response_completion(
    cfg: dict[str, Any],
    messages: list[dict[str, Any]],
    tool_context: AIToolContext | None = None,
) -> str:
    """Responses API 工具循环入口。

    messages 为聊天格式（system 首条 + 历史 + 当前用户消息），内部转换为
    instructions + input items 后按轮次调用，直到模型给出最终文本回复。
    """
    instructions, base_items = _split_instructions(messages)
    input_items: list[dict[str, Any]] = list(base_items)

    all_tools = available_tools(cfg, tool_context)
    user_text = _latest_user_text(messages)
    tools = all_tools if _tool_wanted(user_text) else []
    max_rounds = safe_int(_tools_cfg(cfg).get("max_tool_rounds"), 4, minimum=0, maximum=10)
    if tools and max_rounds > 0:
        await _prefetch_wiki_priority_items(cfg, input_items, tools, tool_context)

    for round_index in range(max_rounds + 1):
        try:
            response = await post_response(cfg, instructions, input_items, tools)
        except AIAgentRequestError as e:
            if tools and e.status_code in {400, 422}:
                logger.warning("[AIAgent] 当前模型接口可能不支持 tools，已降级为普通聊天: %s", e)
                tools = []
                input_items = list(base_items)
                response = await post_response(cfg, instructions, input_items, tools)
            else:
                raise

        calls = _function_call_items(response)
        text = _output_text(response)
        if not calls:
            if text:
                return text
            raise RuntimeError("AI Agent 回复为空。")

        # 把 function_call / web_search_call 原样回传，并执行函数工具
        input_items.extend(_passthrough_items(response))
        for call in calls:
            input_items.append(await _function_call_output_item(cfg, call, tool_context))

        if round_index >= max_rounds:
            if text:
                return text
            # 工具轮次耗尽：移除 tools 强制模型综合已收集的信息给出最终回复
            logger.warning(
                "[AIAgent] 工具调用轮数已达上限，移除 tools 强制生成回复 tools=%s",
                ", ".join(str(item.get("name") or "?") for item in calls) or "?",
            )
            tools = []
            response = await post_response(cfg, instructions, input_items, tools)
            return _output_text(response) or msg("aiagent.tool_limit_reached")

    raise RuntimeError("AI Agent 回复为空。")
