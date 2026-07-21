from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any
from urllib.parse import urlparse

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment

from core.access_control import is_event_allowed
from core.ai_tool_registry import AIToolContext
from core.activity_tracker import ActivityScope
from core.bot_identity import get_bot_name
from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command, is_command_handled, mark_event_handled
from core.stats_tracker import increment as stats_increment

from .client import AIAgentRequestError, request_chat_completion
from .config import get_config, load_persona_prompt
from .memory import (
    append_memory,
    clear_memory,
    clear_session,
    get_history,
    mark_activity,
    memory_paths,
    read_memory_context,
    remember,
    session_key,
    should_summarize,
    summarize_session_memory,
)
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

    # Part 1: 稳定的 system prompt（persona + 固定指令）
    # 所有请求/所有用户都一样 → 触发 DeepSeek 自动前缀缓存，节省 50x 成本
    stable_prompt = load_persona_prompt(cfg)
    extra = str(chat_cfg.get("system_prompt_extra") or "").strip()
    if extra:
        stable_prompt = f"{stable_prompt}\n\n额外要求：\n{extra}"
    stable_prompt = (
        f"{stable_prompt}\n\n"
        "【回复长度要求】\n"
        "每次回复控制在 50 字以内，尽量简短明了。\n"
        "如果需要详细说明（如代码、配置、长解释），只给一句简短结论即可，"
        "完整内容会自动转成合并消息让用户点开查看。\n"
        "不要使用列表、表格、代码块等复杂格式——一句话说清楚。"
    )

    # Part 2: 按会话变化的 memory 上下文（单独放一条 system message，
    # 不影响 Part 1 的缓存前缀）
    memory_context = read_memory_context(event, cfg)

    messages: list[dict[str, str]] = [{"role": "system", "content": stable_prompt}]
    if memory_context:
        messages.append({"role": "system", "content": memory_context})

    history = get_history(session, chat_cfg.get("max_history_messages"))
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})
    return messages


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


_FORWARD_SEGMENT_CHARS = 1500  # chars per node in a combined-forward message


async def _send_long_as_forward(bot: Bot, event: MessageEvent, text: str, total_limit: int) -> None:
    """Split a long reply into segments and send as a merged-forward message."""
    text = text[:total_limit].strip()
    nodes: list[MessageSegment] = []
    bot_name = get_bot_name()
    bot_uid = int(bot.self_id)

    for i in range(0, len(text), _FORWARD_SEGMENT_CHARS):
        segment = text[i : i + _FORWARD_SEGMENT_CHARS].strip()
        if not segment:
            continue
        nodes.append(
            MessageSegment.node_custom(
                user_id=bot_uid,
                nickname=bot_name,
                content=Message(segment),
            )
        )

    if not nodes:
        return

    logger.info("[AIAgent] 回复过长，以合并转发发送 -> %d 个节点", len(nodes))
    try:
        if isinstance(event, GroupMessageEvent):
            await bot.send_group_forward_msg(group_id=event.group_id, messages=nodes)
        else:
            await bot.send_private_forward_msg(user_id=int(event.get_user_id()), messages=nodes)
    except Exception as e:
        logger.warning("[AIAgent] 合并转发发送失败，降级为截断发送: %s", e)
        truncated = text[:total_limit]
        await bot.send(event, Message(truncated))


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
        short_reply_chars = safe_int(chat_cfg.get("short_reply_chars"), 200, minimum=0, maximum=12000)
        remember(session, text, reply, cfg)
        append_memory(event, cfg, text, reply)
        # 检测并异步触发上一轮会话记忆自动总结（空闲 ≥10 分钟时）
        if should_summarize(session):
            asyncio.create_task(summarize_session_memory(cfg, event))
        mark_activity(session)
        if short_reply_chars > 0 and len(reply) > short_reply_chars:
            await _send_long_as_forward(bot, event, reply, min(len(reply), max_reply_chars))
        else:
            await bot.send(event, Message(reply))
        stats_increment(event, "ai_chat_sessions", 1)
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


# ── 隐藏命令：查看 / 总结记忆 ─────────────────────────────────────────


@command(
    "查看记忆",
    aliases=("看记忆", "记忆", "memory"),
    description="",
    usage="查看记忆",
    show_in_help=False,
    require_tome=True,
)
async def handle_view_memory(ctx: CommandContext) -> None:
    """查看当前会话的持久化记忆内容（含摘要和原始记录）。"""
    cfg = get_config()
    if not cfg.get("enabled", False):
        await ctx.send(Message("AI Agent 未启用"))
        return

    blocks: list[str] = []
    for label, path in memory_paths(ctx.event, cfg):
        if not path.is_file():
            blocks.append(f"📋 {label}\n（暂无记忆）")
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            blocks.append(f"📋 {label}\n（读取失败）")
            continue
        if not content:
            blocks.append(f"📋 {label}\n（暂无记忆）")
            continue
        # Keep the display compact — show last ~2000 chars
        if len(content) > 2000:
            display = f"⋯（内容较长，仅显示末尾）\n{content[-2000:]}"
        else:
            display = content
        blocks.append(f"📋 {label}\n{display}")

    await ctx.send(Message("\n\n".join(blocks)))


@command(
    "总结记忆",
    aliases=("总结", "summarize"),
    description="",
    usage="总结记忆",
    show_in_help=False,
    require_tome=True,
)
async def handle_summarize_memory(ctx: CommandContext) -> None:
    """手动触发当前会话的原始对话记忆总结。"""
    cfg = get_config()
    if not cfg.get("enabled", False):
        await ctx.send(Message("AI Agent 未启用"))
        return

    await ctx.send(Message("⏳ 正在总结记忆，请稍候..."))
    result = await summarize_session_memory(cfg, ctx.event, force=True)
    await ctx.send(Message(result))
