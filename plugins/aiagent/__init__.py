from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent

from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command, is_command_handled

from .config import get_config, load_persona_prompt

logger = logging.getLogger("HikariBot.AIAgent")

_histories: dict[str, list[dict[str, str]]] = {}
_last_used_at: dict[str, float] = {}


class AIAgentRequestError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"AI Agent 请求失败: HTTP {status_code} {detail}")
        self.status_code = status_code
        self.detail = detail


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


def _safe_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return min(max(parsed, minimum), maximum)


def _safe_float(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        return default
    return min(max(parsed, minimum), maximum)


def _session_key(event: MessageEvent) -> str:
    if isinstance(event, GroupMessageEvent):
        return f"group:{event.group_id}"
    return f"private:{event.get_user_id()}"


def _check_cooldown(user_id: str, cooldown_seconds: Any) -> int:
    cooldown = _safe_int(cooldown_seconds, 3, minimum=0, maximum=3600)
    if cooldown <= 0:
        return 0
    now = time.monotonic()
    remain = int(cooldown - (now - _last_used_at.get(user_id, 0.0)))
    if remain > 0:
        return remain
    _last_used_at[user_id] = now
    return 0


def _endpoint(base_url: Any) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        base = "https://api.openai.com/v1"
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _trim_history(history: list[dict[str, str]], max_messages: Any) -> list[dict[str, str]]:
    limit = _safe_int(max_messages, 10, minimum=0, maximum=40)
    if limit <= 0:
        return []
    return history[-limit:]


def _build_messages(cfg: dict[str, Any], session_key: str, user_text: str) -> list[dict[str, str]]:
    chat_cfg = cfg.get("chat") if isinstance(cfg.get("chat"), dict) else {}
    system_prompt = load_persona_prompt(cfg)
    extra = str(chat_cfg.get("system_prompt_extra") or "").strip()
    if extra:
        system_prompt = f"{system_prompt}\n\n额外要求：\n{extra}"

    history = _trim_history(_histories.get(session_key, []), chat_cfg.get("max_history_messages"))
    return [{"role": "system", "content": system_prompt}, *history, {"role": "user", "content": user_text}]


async def _request_chat_completion(cfg: dict[str, Any], messages: list[dict[str, str]]) -> str:
    model_cfg = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
    api_key = str(model_cfg.get("api_key") or "").strip()
    model = str(model_cfg.get("model") or "").strip()
    if not model:
        raise RuntimeError("AI Agent 模型名称未配置。")

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": _safe_float(model_cfg.get("temperature"), 0.7, minimum=0.0, maximum=2.0),
        "top_p": _safe_float(model_cfg.get("top_p"), 1.0, minimum=0.0, maximum=1.0),
        "max_tokens": _safe_int(model_cfg.get("max_tokens"), 1024, minimum=1, maximum=32000),
    }
    extra_body = model_cfg.get("extra_body")
    if isinstance(extra_body, dict):
        payload.update(extra_body)

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    timeout = httpx.Timeout(_safe_int(model_cfg.get("timeout_seconds"), 60, minimum=5, maximum=600))
    proxy = str(model_cfg.get("proxy") or "").strip() or None
    async with httpx.AsyncClient(timeout=timeout, proxy=proxy) as client:
        response = await client.post(_endpoint(model_cfg.get("base_url")), headers=headers, json=payload)
    if response.status_code >= 400:
        raise AIAgentRequestError(response.status_code, response.text[:400])

    data = response.json()
    choices = data.get("choices") if isinstance(data, dict) else None
    if not choices:
        raise RuntimeError("AI Agent 返回结果为空。")
    message = choices[0].get("message") if isinstance(choices[0], dict) else {}
    content = str(message.get("content") or "").strip()
    if not content:
        raise RuntimeError("AI Agent 回复为空。")
    return content


def _remember(session_key: str, user_text: str, assistant_text: str, cfg: dict[str, Any]) -> None:
    chat_cfg = cfg.get("chat") if isinstance(cfg.get("chat"), dict) else {}
    history = _histories.setdefault(session_key, [])
    history.extend([
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": assistant_text},
    ])
    _histories[session_key] = _trim_history(history, chat_cfg.get("max_history_messages"))


async def _handle_chat_event(bot: Bot, event: MessageEvent, text: str, *, show_usage: bool) -> None:
    cfg = get_config()
    if not cfg.get("enabled", False):
        if show_usage:
            await bot.send(event, Message(msg("aiagent.disabled")))
        return

    text = _normalize_text(text)
    if not text:
        if show_usage:
            await bot.send(event, Message(msg("aiagent.usage")))
        return
    if text.casefold() in {"reset", "重置", "清空上下文"}:
        _histories.pop(_session_key(event), None)
        await bot.send(event, Message(msg("aiagent.reset_done")))
        return

    chat_cfg = cfg.get("chat") if isinstance(cfg.get("chat"), dict) else {}
    max_user_chars = _safe_int(chat_cfg.get("max_user_chars"), 2000, minimum=1, maximum=20000)
    if len(text) > max_user_chars:
        await bot.send(event, Message(msg("aiagent.too_long", max_chars=max_user_chars)))
        return

    remain = _check_cooldown(event.get_user_id(), chat_cfg.get("cooldown_seconds", 3))
    if remain > 0:
        await bot.send(event, Message(msg("aiagent.cooldown", seconds=remain)))
        return

    session_key = _session_key(event)
    try:
        messages = _build_messages(cfg, session_key, text)
        reply = await _request_chat_completion(cfg, messages)
        max_reply_chars = _safe_int(chat_cfg.get("max_reply_chars"), 3500, minimum=100, maximum=12000)
        if len(reply) > max_reply_chars:
            reply = f"{reply[:max_reply_chars].rstrip()}\n\n{msg('aiagent.reply_truncated')}"
        _remember(session_key, text, reply, cfg)
        await bot.send(event, Message(reply))
    except AIAgentRequestError as e:
        logger.warning("[AIAgent] API 请求失败: %s", e)
        if e.status_code in {401, 403}:
            await bot.send(event, Message(msg("aiagent.auth_failed")))
        else:
            await bot.send(event, Message(msg("aiagent.failed")))
    except Exception as e:
        logger.exception("[AIAgent] 聊天失败: %s", e)
        await bot.send(event, Message(msg("aiagent.failed")))


async def _handle_chat(ctx: CommandContext) -> None:
    await _handle_chat_event(ctx.bot, ctx.event, ctx.args, show_usage=True)


def _should_auto_reply(event: MessageEvent) -> bool:
    if isinstance(event, GroupMessageEvent):
        return event.is_tome()
    return True


aiagent_auto_matcher = on_message(priority=2, block=False)


@aiagent_auto_matcher.handle()
async def _handle_auto_chat(bot: Bot, event: MessageEvent) -> None:
    if is_command_handled(event) or not _should_auto_reply(event):
        return
    await _handle_chat_event(bot, event, event.get_plaintext(), show_usage=False)


@command("ai", aliases=("aiagent", "聊天"), description="和配置的 AI Agent 聊天", usage="ai <内容>", require_tome=True)
async def cmd_aiagent(ctx: CommandContext) -> None:
    await _handle_chat(ctx)


@command("ai重置", aliases=("ai reset", "聊天重置"), description="清空当前会话的 AI Agent 上下文", usage="ai重置", require_tome=True)
async def cmd_aiagent_reset(ctx: CommandContext) -> None:
    _histories.pop(_session_key(ctx.event), None)
    await ctx.send(Message(msg("aiagent.reset_done")))
