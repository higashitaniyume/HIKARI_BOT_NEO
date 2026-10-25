from __future__ import annotations

import asyncio
import copy
import json
import logging
from typing import Any

import httpx

from core.ai_tool_registry import AIToolContext
from core.bot_messages import get_message as msg

from .config import api_protocol
from .tools import available_tools, execute_tool_call
from .usage import log_chat_usage
from .utils import parse_dsml_tool_calls, safe_float, safe_int, strip_dsml_tags
from .vision import has_images, request_model, strip_images
from .wiki import _prefetch_wiki_priority_tools

logger = logging.getLogger("HikariBot.AIAgent.Client")


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


def _extract_message_parts(message: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """解析 assistant message 的 tool_calls / content（含 DSML 降级）。"""
    tool_calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
    content = str(message.get("content") or "").strip()
    # DeepSeek V4 Flash DSML 降级: 思考模式下 API 可能将工具调用以 DSML
    # 标签形式嵌入 content 而非标准 tool_calls 字段，需手动解析。
    if not tool_calls and content:
        dsml_calls = parse_dsml_tool_calls(content)
        if dsml_calls:
            logger.debug("[AIAgent] 从 DSML 内容解析到 %d 个工具调用", len(dsml_calls))
            cleaned_content = strip_dsml_tags(content)
            message["content"] = cleaned_content or ""
            message["tool_calls"] = dsml_calls
            tool_calls = dsml_calls
            content = str(message["content"]).strip()
    return tool_calls, content


def _empty_retry_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    """空回复重试：关闭思考 + 至少翻倍输出预算。"""
    retry_cfg = copy.deepcopy(cfg)
    model_cfg = retry_cfg.get("model") if isinstance(retry_cfg.get("model"), dict) else {}
    current = safe_int(model_cfg.get("max_tokens"), 8192, minimum=1, maximum=131072)
    model_cfg["max_tokens"] = min(max(current * 2, 16384), 131072)
    retry_cfg["model"] = model_cfg
    thinking_cfg = retry_cfg.get("thinking") if isinstance(retry_cfg.get("thinking"), dict) else {}
    thinking_cfg["enabled"] = False
    retry_cfg["thinking"] = thinking_cfg
    return retry_cfg


_MODEL_SEMAPHORES: dict[asyncio.AbstractEventLoop, tuple[int, asyncio.Semaphore]] = {}


def _model_semaphore(cfg: dict[str, Any]) -> asyncio.Semaphore:
    """全局模型并发上限，避免高峰期把上游打到 429。"""
    chat_cfg = cfg.get("chat") if isinstance(cfg.get("chat"), dict) else {}
    size = safe_int(chat_cfg.get("max_concurrent_requests"), 8, minimum=1, maximum=64)
    loop = asyncio.get_running_loop()
    entry = _MODEL_SEMAPHORES.get(loop)
    if entry is None or entry[0] != size:
        entry = (size, asyncio.Semaphore(size))
        _MODEL_SEMAPHORES[loop] = entry
    return entry[1]


async def post_chat_completion(
    cfg: dict[str, Any],
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    caller: str = "chat",
) -> dict[str, Any]:
    model_cfg = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
    api_key = str(model_cfg.get("api_key") or "").strip()
    # 带图请求可能切换到专门的视觉模型（见 vision.request_model）。
    model = request_model(cfg, has_images(messages))
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
    if isinstance(data, dict):
        log_chat_usage(caller, model, data.get("usage"))
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
    *,
    caller: str = "chat",
) -> str:
    """AI Agent 对话入口：先过全局并发闸门，再按协议分发。"""
    async with _model_semaphore(cfg):
        return await _request_chat_completion(cfg, messages, tool_context, caller=caller)


async def _request_chat_completion(
    cfg: dict[str, Any],
    messages: list[dict[str, str]],
    tool_context: AIToolContext | None = None,
    *,
    caller: str = "chat",
) -> str:
    """AI Agent 对话入口。

    按配置的 API 协议分发：`responses` 走 DeepSeek Responses API，
    `chat_completions` 走 OpenAI Chat Completions 兼容协议。搜索工具由本地
    函数工具执行（见 tools/search.py），两种协议行为一致。
    """
    if api_protocol(cfg) == "responses":
        from .responses_client import request_response_completion

        return await request_response_completion(cfg, messages, tool_context, caller=caller)

    plain_request_messages: list[dict[str, Any]] = [dict(message) for message in messages]
    request_messages: list[dict[str, Any]] = [dict(message) for message in messages]
    all_tools = available_tools(cfg, tool_context)
    max_rounds = safe_int(_tools_cfg(cfg).get("max_tool_rounds"), 4, minimum=0, maximum=10)
    # 工具集合必须逐请求稳定：寒暄不下发 tools、普通消息下发，会让同一会话的
    # 请求前缀在「有工具 / 无工具」之间来回切换，整段缓存都失效。
    # 是否真的调用工具交给 system prompt 约束和模型判断。
    tools = all_tools if max_rounds > 0 else []
    if tools:
        await _prefetch_wiki_priority_tools(cfg, request_messages, tools, tool_context)

    empty_retried = False
    for round_index in range(max_rounds + 1):
        try:
            message = await post_chat_completion(cfg, request_messages, tools, caller=caller)
        except AIAgentRequestError as e:
            if e.status_code in {400, 422} and has_images(request_messages):
                # 非视觉模型会明确拒绝图片输入，去掉图片再试一次。
                logger.warning("[AIAgent] 当前模型可能不支持图片输入，已去掉图片重试: %s", e)
                plain_request_messages = strip_images(plain_request_messages)
                request_messages = strip_images(request_messages)
                message = await post_chat_completion(cfg, request_messages, tools, caller=caller)
            elif tools and e.status_code in {400, 422}:
                logger.warning("[AIAgent] 当前模型接口可能不支持 tools，已降级为普通聊天: %s", e)
                tools = []
                request_messages = [dict(message) for message in plain_request_messages]
                message = await post_chat_completion(cfg, request_messages, tools, caller=caller)
            else:
                raise
        tool_calls, content = _extract_message_parts(message)
        if not tool_calls and not content and not empty_retried:
            # 与 Responses 侧一致：思考模式可能把 max_tokens 耗在 reasoning 上。
            empty_retried = True
            logger.warning("[AIAgent] Chat Completions 返回空内容，关闭思考后重试")
            message = await post_chat_completion(
                _empty_retry_cfg(cfg), request_messages, tools, caller=caller
            )
            tool_calls, content = _extract_message_parts(message)
            if not tool_calls and not content:
                logger.warning("[AIAgent] Chat Completions 空回复重试仍失败")
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
            message = await post_chat_completion(cfg, request_messages, tools, caller=caller)
            content = str(message.get("content") or "").strip()
            return content or msg("aiagent.tool_limit_reached")

        request_messages.append(_assistant_tool_message(message))
        for tool_call in tool_calls:
            if isinstance(tool_call, dict):
                request_messages.append(await execute_tool_call(cfg, tool_call, tool_context))

    raise RuntimeError("AI Agent 回复为空。")
