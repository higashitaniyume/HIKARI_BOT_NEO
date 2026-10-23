"""DeepSeek 用量日志：记录每次请求的缓存命中情况。

- Chat Completions: ``usage.prompt_cache_hit_tokens`` / ``prompt_cache_miss_tokens``
- Responses API: ``usage.input_tokens_details.cached_tokens``
官方文档：https://api-docs.deepseek.com/zh-cn/guides/kv_cache/

命中率低的时候，用 ``grep "[AIAgent] usage"`` 按 caller 归因：
chat（正常聊天）、review（群风控/出站审查）、summarize（记忆总结）。
"""

from __future__ import annotations

import logging
from typing import Any

from .utils import safe_int

logger = logging.getLogger("HikariBot.AIAgent.Usage")

_MAX_TOKENS = 10**9


def _rate(hit: int, total: int) -> str:
    return f"{hit / total * 100:.1f}%" if total > 0 else "-"


def _log(caller: str, model: str, prompt: int, hit: int, miss: int, output: int) -> None:
    logger.info(
        "[AIAgent] usage caller=%s model=%s input=%d hit=%d miss=%d hit_rate=%s output=%d",
        caller or "-",
        model or "-",
        prompt,
        hit,
        miss,
        _rate(hit, max(hit + miss, prompt)),
        output,
    )


def log_chat_usage(caller: str, model: str, usage: Any) -> None:
    if not isinstance(usage, dict) or not usage:
        return
    hit = safe_int(usage.get("prompt_cache_hit_tokens"), 0, minimum=0, maximum=_MAX_TOKENS)
    miss = safe_int(usage.get("prompt_cache_miss_tokens"), 0, minimum=0, maximum=_MAX_TOKENS)
    prompt = safe_int(usage.get("prompt_tokens"), hit + miss, minimum=0, maximum=_MAX_TOKENS)
    output = safe_int(usage.get("completion_tokens"), 0, minimum=0, maximum=_MAX_TOKENS)
    _log(caller, model, prompt, hit, miss, output)


def log_responses_usage(caller: str, model: str, usage: Any) -> None:
    if not isinstance(usage, dict) or not usage:
        return
    details = usage.get("input_tokens_details")
    hit = safe_int(
        details.get("cached_tokens") if isinstance(details, dict) else 0,
        0,
        minimum=0,
        maximum=_MAX_TOKENS,
    )
    prompt = safe_int(usage.get("input_tokens"), 0, minimum=0, maximum=_MAX_TOKENS)
    output = safe_int(usage.get("output_tokens"), 0, minimum=0, maximum=_MAX_TOKENS)
    _log(caller, model, prompt, hit, max(prompt - hit, 0), output)
