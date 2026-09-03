"""一次性 JSON 判定：拿 AI Agent 的模型配置问一个是/否问题。

内容审查类插件（群风控、出站自审查）共用这里的请求封装：复用 AI Agent 的
base_url / api_key / model / proxy，但强制关闭思考模式和工具调用，只要一次极短
的 JSON 回答，尽量压低费用和延迟。
"""

from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from .client import post_chat_completion
from .config import get_config

logger = logging.getLogger("HikariBot.AIAgent.Review")


@dataclass(frozen=True, slots=True)
class ReviewResult:
    risk: bool
    reason: str
    model: str = ""


def model_label() -> str:
    model_cfg = get_config().get("model")
    if not isinstance(model_cfg, dict):
        return ""
    return str(model_cfg.get("model") or "").strip()


async def request_verdict(text: str, review_cfg: dict[str, Any], *, label: str = "review") -> ReviewResult | None:
    """返回判定结果；模型未配置或返回无法解析时返回 None（调用方按放行处理）。

    Args:
        text: 待判定文本。
        review_cfg: 需要 prompt / max_chars / temperature / max_tokens / timeout_seconds。
        label: 日志前缀，用来区分调用方。
    """
    agent_cfg = get_config()
    model_cfg = agent_cfg.get("model") if isinstance(agent_cfg.get("model"), dict) else {}
    if not str(model_cfg.get("model") or "").strip():
        logger.warning("[%s] AI Agent 未配置模型名称，跳过判定", label)
        return None
    if not str(model_cfg.get("api_key") or "").strip():
        logger.warning("[%s] AI Agent 未配置 API Key，跳过判定", label)
        return None

    request_cfg = _build_request_config(agent_cfg, review_cfg)
    max_chars = int(review_cfg.get("max_chars") or 800)
    message = await post_chat_completion(
        request_cfg,
        [
            {"role": "system", "content": str(review_cfg.get("prompt") or "")},
            {"role": "user", "content": text[:max_chars]},
        ],
        tools=[],
    )
    content = str(message.get("content") or "").strip()
    if not content:
        logger.warning("[%s] 判定模型返回空内容", label)
        return None

    data = _parse_json_response(content)
    if data is None:
        logger.warning("[%s] 判定模型返回无法解析: %r", label, content[:200])
        return None

    return ReviewResult(
        risk=_as_bool(data.get("risk")),
        reason=re.sub(r"\s+", " ", str(data.get("reason") or "")).strip()[:60],
        model=str(request_cfg.get("model", {}).get("model") or ""),
    )


def _build_request_config(agent_cfg: dict[str, Any], review_cfg: dict[str, Any]) -> dict[str, Any]:
    request_cfg = copy.deepcopy(agent_cfg)
    model_cfg = dict(request_cfg.get("model") if isinstance(request_cfg.get("model"), dict) else {})
    model_cfg["temperature"] = float(review_cfg.get("temperature") or 0.0)
    model_cfg["max_tokens"] = int(review_cfg.get("max_tokens") or 300)
    model_cfg["timeout_seconds"] = int(review_cfg.get("timeout_seconds") or 20)
    model_cfg["tool_choice"] = None
    request_cfg["model"] = model_cfg
    request_cfg["thinking"] = {"enabled": False}
    request_cfg["tools"] = {"max_tool_rounds": 0}
    return request_cfg


def _parse_json_response(content: str) -> dict[str, Any] | None:
    text = content.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "yes", "是", "敏感"}
