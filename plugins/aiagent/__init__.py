from __future__ import annotations

import logging
import re
import time
from typing import Any
from urllib.parse import urlparse

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent

from core.access_control import is_event_allowed
from core.ai_tool_registry import AIToolContext
from core.activity_tracker import ActivityScope
from core.bot_messages import get_message as msg
from core.command_router import is_command_handled, mark_event_handled

from .client import AIAgentRequestError, request_chat_completion
from .config import get_config, load_persona_prompt
from .memory import append_memory, clear_memory, clear_session, get_history, remember, session_key
from .memory import read_memory_context
from .tools import available_tools, execute_tool_call
from .utils import normalize_text, safe_int, strip_markdown

logger = logging.getLogger("HikariBot.AIAgent")

_last_used_at: dict[str, float] = {}
_URL_PATTERN = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"]+|\b(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s<>\"]*)?")

# Backward-compatible aliases for tests and plugin-local imports.
_request_chat_completion = request_chat_completion
_available_tools = available_tools
_execute_tool_call = execute_tool_call

# Ensure config file exists on plugin load (creates aiagent.json with defaults,
# including permissions block, so the admin permissions page can discover it).
get_config()


def _check_cooldown(user_id: str, cooldown_seconds: Any) -> int:
    cooldown = safe_int(cooldown_seconds, 3, minimum=0, maximum=3600)
    if cooldown <= 0:
        return 0
    now = time.monotonic()
    remain = int(cooldown - (now - _last_used_at.get(user_id, 0.0)))
    if remain > 0:
        return remain
    _last_used_at[user_id] = now
    return 0


def _build_messages(cfg: dict[str, Any], event: MessageEvent, session: str, user_text: str) -> list[dict[str, str]]:
    chat_cfg = cfg.get("chat") if isinstance(cfg.get("chat"), dict) else {}
    system_prompt = load_persona_prompt(cfg)
    memory_context = read_memory_context(event, cfg)
    if memory_context:
        system_prompt = f"{system_prompt}\n\n{memory_context}"
    extra = str(chat_cfg.get("system_prompt_extra") or "").strip()
    if extra:
        system_prompt = f"{system_prompt}\n\n额外要求：\n{extra}"

    history = get_history(session, chat_cfg.get("max_history_messages"))
    return [{"role": "system", "content": system_prompt}, *history, {"role": "user", "content": user_text}]


def _url_host(raw_url: str) -> str:
    value = raw_url.strip().rstrip(".,;!?，。！？；")
    if value.startswith("www."):
        value = f"https://{value}"
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    return (parsed.netloc or "").split("@")[-1].split(":")[0].lower()


def _is_blocked_media_link(text: str, cfg: dict[str, Any]) -> bool:
    chat_cfg = cfg.get("chat") if isinstance(cfg.get("chat"), dict) else {}
    domains = chat_cfg.get("blocked_url_domains") if isinstance(chat_cfg.get("blocked_url_domains"), list) else []
    blocked = [str(domain).strip().lower().lstrip(".") for domain in domains if str(domain).strip()]
    if not blocked:
        return False

    for match in _URL_PATTERN.finditer(text):
        host = _url_host(match.group(0))
        if any(host == domain or host.endswith(f".{domain}") for domain in blocked):
            return True
    return False


async def _handle_chat_event(bot: Bot, event: MessageEvent, text: str) -> None:
    text = normalize_text(text)
    cfg = get_config()
    if not text:
        return
    if _is_blocked_media_link(text, cfg):
        return

    session = session_key(event)
    if text.casefold() in {"reset", "重置", "清空上下文"}:
        clear_session(session)
        clear_memory(event, cfg)
        await bot.send(event, Message(msg("aiagent.reset_done")))
        mark_event_handled(event)
        return

    chat_cfg = cfg.get("chat") if isinstance(cfg.get("chat"), dict) else {}
    max_user_chars = safe_int(chat_cfg.get("max_user_chars"), 2000, minimum=1, maximum=20000)
    if len(text) > max_user_chars:
        await bot.send(event, Message(msg("aiagent.too_long", max_chars=max_user_chars)))
        mark_event_handled(event)
        return

    remain = _check_cooldown(event.get_user_id(), chat_cfg.get("cooldown_seconds", 3))
    if remain > 0:
        await bot.send(event, Message(msg("aiagent.cooldown", seconds=remain)))
        mark_event_handled(event)
        return

    try:
        messages = _build_messages(cfg, event, session, text)
        user_preview = text[:40].replace("\n", " ")
        with ActivityScope("aiagent", "replying", "回复用户", description=user_preview):
            reply = await request_chat_completion(cfg, messages, AIToolContext(bot=bot, event=event, agent_config=cfg))
        reply = strip_markdown(reply)
        max_reply_chars = safe_int(chat_cfg.get("max_reply_chars"), 3500, minimum=100, maximum=12000)
        if len(reply) > max_reply_chars:
            reply = f"{reply[:max_reply_chars].rstrip()}\n\n{msg('aiagent.reply_truncated')}"
        remember(session, text, reply, cfg)
        append_memory(event, cfg, text, reply)
        await bot.send(event, Message(reply))
        mark_event_handled(event)
    except AIAgentRequestError as e:
        logger.warning("[AIAgent] API 请求失败: %s", e)
        if e.status_code in {401, 403}:
            await bot.send(event, Message(msg("aiagent.auth_failed")))
        else:
            await bot.send(event, Message(msg("aiagent.failed")))
        mark_event_handled(event)
    except Exception as e:
        logger.exception("[AIAgent] 聊天失败: %s", e)
        await bot.send(event, Message(msg("aiagent.failed")))
        mark_event_handled(event)


def _should_auto_reply(event: MessageEvent) -> bool:
    if isinstance(event, GroupMessageEvent):
        return event.is_tome()
    return True


aiagent_auto_matcher = on_message(priority=99, block=False)


@aiagent_auto_matcher.handle()
async def _handle_auto_chat(bot: Bot, event: MessageEvent) -> None:
    if is_command_handled(event) or not _should_auto_reply(event):
        return

    cfg = get_config()
    if not cfg.get("enabled", False):
        return
    if not is_event_allowed(cfg, event):
        logger.info(
            "[AIAgent] 权限规则已阻止回复 -> user=%s group=%s",
            event.get_user_id(),
            getattr(event, "group_id", ""),
        )
        return

    await _handle_chat_event(bot, event, event.get_plaintext())
