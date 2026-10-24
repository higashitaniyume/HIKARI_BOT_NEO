from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import re
import time
from typing import Any
from urllib.parse import urlparse

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment
import httpx

from core.access_control import is_event_allowed
from core.ai_tool_registry import AIToolContext
from core.activity_tracker import ActivityScope
from core.bot_identity import get_bot_name
from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command, is_command_handled, mark_event_handled
from core.stats_tracker import increment as stats_increment

from . import auto_reply
from . import chatlog  # noqa: F401 - 导入即注册被动聊天记录 matcher
from .client import AIAgentRequestError, request_chat_completion
from .config import get_config, get_config_for_event
from .persona import load_persona_prompt
from .quota import get_quota_status, refund_quota, reserve_quota
from .memory import (
    append_memory,
    clear_memory,
    clear_session,
    get_history,
    mark_activity,
    memory_paths,
    read_memory_context,
    read_shared_context,
    remember,
    remember_shared,
    session_key,
    should_summarize,
    summarize_session_memory,
)
from .tools import available_tools, execute_tool_call
from .utils import normalize_text, safe_bool, safe_int, strip_markdown
from .vision import build_image_blocks, collect_image_urls, request_model, text_block

logger = logging.getLogger("HikariBot.AIAgent")

_last_used_at: dict[str, float] = {}
_session_locks: dict[str, asyncio.Lock] = {}
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


_WEEKDAYS = ("一", "二", "三", "四", "五", "六", "日")


def _time_notice(now: datetime | None = None) -> str:
    """当前时间提示。

    放在最后一条用户消息里，而不是 system prompt 里：system 和短期历史保持
    逐字节不变，每轮变化只影响尾部，前面的大段前缀才能命中 DeepSeek 缓存。
    """
    now = now or datetime.now()
    return f"【当前时间】{now.strftime('%Y-%m-%d %H:%M')} 星期{_WEEKDAYS[now.weekday()]}"


def _build_messages(
    cfg: dict[str, Any],
    event: MessageEvent,
    session: str,
    user_text: str,
    image_blocks: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    chat_cfg = cfg.get("chat") if isinstance(cfg.get("chat"), dict) else {}

    # Part 1: 稳定的 system prompt（persona + 固定指令）
    # 所有请求/所有用户都一样 → 触发 DeepSeek 自动前缀缓存，节省 50x 成本
    stable_prompt = load_persona_prompt(cfg)
    extra = str(chat_cfg.get("system_prompt_extra") or "").strip()
    if extra:
        stable_prompt = f"{stable_prompt}\n\n额外要求：\n{extra}"
    tools_cfg = cfg.get("tools") if isinstance(cfg.get("tools"), dict) else {}
    has_workspace = safe_bool(tools_cfg.get("workspace", {}).get("enabled"), True)
    has_images = safe_bool(tools_cfg.get("images", {}).get("enabled"), True)
    file_image_prompt = ""
    if has_workspace or has_images:
        file_image_prompt = (
            "\n\n【文件与图片】\n"
            "你可以使用工作区读写文件；生成或处理好的文件用 send_file 发出。\n"
            "需要图片时先用 web_image_search 搜图，再用 send_images 发送，不要凭空编造图片链接。\n"
            "工作区只属于当前会话，路径一律使用相对路径。"
        )

    stable_prompt = (
        f"{stable_prompt}\n\n"
        "【回复长度】\n"
        "日常闲聊保持简洁；用户要教程、代码、长文分析时可以完整回答，不要自行截断。\n"
        "超过单条消息上限会自动拆成合并聊天记录，请用自然段组织内容，方便按段落拆分。\n\n"
        "【能力说明】\n"
        "当用户询问你会干什么、有什么功能、某个功能怎么使用时，\n"
        "调用 bot_help 工具查询帮助文档后再回答，不要凭记忆编造。\n\n"
        "【工具使用】\n"
        "寒暄、道谢、情绪回应、简短确认不需要调用工具，直接自然回复；\n"
        "只有确实需要外部信息或群资料时才调用工具。\n"
        "如果工具列表里有 send_voice，用户想听语音、或一句话用语音说更自然时可以调用；"
        "语音内容要短、口语化，不要念 Markdown、代码、链接或长段落，调用后文字回复不要再重复语音内容。\n\n"
        "【时间说明】\n"
        "用户消息开头的【当前时间】是系统自动附加的，不是用户原话；\n"
        "回答与日期、时间有关的问题时以它为准，不要复述这个标记。"
        f"{file_image_prompt}"
    )

    messages: list[dict[str, Any]] = [{"role": "system", "content": stable_prompt}]

    # Part 2: 长期记忆（只取稳定摘要区），放在历史之前。
    # 这部分只在写摘要时变化，和后面追加的历史一起构成稳定的请求前缀。
    memory_context = read_memory_context(event, cfg)
    if memory_context:
        messages.append({"role": "system", "content": memory_context})

    history = get_history(
        session,
        chat_cfg.get("max_history_messages"),
        chat_cfg.get("max_context_chars"),
    )
    messages.extend(history)

    # Part 3: 群聊公共上下文（默认关闭）。它每轮都可能变，放到历史之后，
    # 避免顶掉前面已经稳定的前缀。
    shared_context = read_shared_context(event, cfg)
    if shared_context:
        messages.append({"role": "system", "content": shared_context})

    # 图片只能挂在 user 消息上（system / assistant 带图会被 API 拒绝）。
    # 当前时间由调用方拼进 user_text，保证「发送内容 = 写入历史的内容」。
    if image_blocks:
        messages.append({"role": "user", "content": [text_block(user_text), *image_blocks]})
    else:
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


def _quote_reply_enabled(chat_cfg: dict[str, Any]) -> bool:
    """`chat.quote_reply` 默认开启；兼容字符串形式的 true/false。"""
    value = chat_cfg.get("quote_reply", True)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _build_reply_message(event: MessageEvent, text: str, cfg: dict[str, Any]) -> Message:
    """构造 AI 回复消息：默认以引用回复的方式挂在触发消息下。"""
    message = Message(text)
    chat_cfg = cfg.get("chat") if isinstance(cfg.get("chat"), dict) else {}
    if not _quote_reply_enabled(chat_cfg):
        return message
    message_id = getattr(event, "message_id", None)
    if message_id is None or not str(message_id).strip():
        return message
    return Message(MessageSegment.reply(message_id)) + message


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


async def _handle_chat_event_unlocked(bot: Bot, event: MessageEvent, text: str, *, auto: bool = False) -> None:
    text = normalize_text(text)
    cfg = get_config_for_event(event)

    # 如果当前消息是引用回复，把被引用的消息内容附加到输入中
    reply = getattr(event, "reply", None)
    if reply is not None and reply.message:
        quoted_text = reply.message.extract_plain_text().strip()
        if quoted_text:
            text = f"（用户引用了一条消息：「{quoted_text[:200]}」）\n{text}"
            logger.debug("[AIAgent] 注入引用内容: %.80s", quoted_text)

    from .attachments import collect_and_save_attachments

    attachment_prompts = await collect_and_save_attachments(bot, event, cfg, is_auto_reply=auto)
    if attachment_prompts:
        att_prefix = "\n".join(attachment_prompts)
        text = f"{att_prefix}\n{text}".strip() if text else att_prefix

    image_urls = collect_image_urls(event, cfg)
    if not text and not image_urls:
        return
    if _is_blocked_media_link(text, cfg):
        return

    session = session_key(event)
    if text.casefold() in {"reset", "重置", "清空上下文"}:
        # 自动回复时不要把群友随口说的「重置」当成控制指令；要重置请 @ 机器人。
        if auto:
            return
        clear_session(session)
        clear_memory(event, cfg)
        await bot.send(event, Message(msg("aiagent.reset_done")))
        mark_event_handled(event)
        return

    chat_cfg = cfg.get("chat") if isinstance(cfg.get("chat"), dict) else {}
    max_user_chars = safe_int(chat_cfg.get("max_user_chars"), 2000, minimum=1, maximum=20000)
    if len(text) > max_user_chars:
        if auto:
            return
        await bot.send(event, Message(msg("aiagent.too_long", max_chars=max_user_chars)))
        mark_event_handled(event)
        return

    # 自动回复只受同群冷却约束（在 matcher 里已经检查），不再按用户冷却，
    # 否则同一个人连发两条时第二条会被悄悄丢掉。
    if not auto:
        remain = _check_cooldown(event.get_user_id(), chat_cfg.get("cooldown_seconds", 3))
        if remain > 0:
            await bot.send(event, Message(msg("aiagent.cooldown", seconds=remain)))
            mark_event_handled(event)
            return

    # 配额：原子「检查 + 预留」，失败（未成功回复）时在 finally 里退回。
    # 自动回复是机器人主动接话，不消耗用户/群配额；成本由白名单和冷却控制。
    quota_block: dict[str, Any] | None = None
    quota_reserved = False
    if not auto:
        quota_block, quota_reserved = reserve_quota(cfg, event)
    if quota_block is not None:
        block = dict(quota_block)
        block["who"] = "群聊" if block.get("who") == "group" else "私聊"
        block["period"] = "今日" if block.get("period") == "day" else "本小时"
        block["used"] = f"{block['used']:,}"
        block["limit"] = f"{block['limit']:,}"
        await bot.send(event, Message(msg("aiagent.quota_exhausted", **block)))
        mark_event_handled(event)
        return

    delivered = False
    request_timeout = 120
    try:
        image_blocks = await build_image_blocks(image_urls, cfg)
        if image_blocks:
            logger.info(
                "[AIAgent] 附带 %d 张图片送模型（%s） -> %s",
                len(image_blocks),
                request_model(cfg, True),
                session,
            )
            if not text:
                text = msg("aiagent.vision_image_only")
        elif not text:
            return
        # 时间只拼进这一条 user 消息，并原样写入短期历史，保证下一轮的
        # 历史前缀和本轮实际发送的内容逐字节一致。
        prompt_text = f"{_time_notice()}\n{text}"
        messages = _build_messages(cfg, event, session, prompt_text, image_blocks)

        user_preview = text[:40].replace("\n", " ")
        profile_name = str(cfg.get("_profile_name") or "")
        # 单条消息的总预算：模型调用 + 工具轮次都在这段时间内完成。
        # 没有总预算时，工具循环可能把同一个用户的会话锁占住十几分钟。
        request_timeout = safe_int(
            chat_cfg.get("request_timeout_seconds"),
            120,
            minimum=15,
            maximum=600,
        )
        logger.info("[AIAgent] 使用配置「%s」回复 -> %s", profile_name, session)
        with ActivityScope(
            "aiagent", "replying", "回复用户", description=user_preview, profile=profile_name
        ):
            reply = await asyncio.wait_for(
                request_chat_completion(
                    cfg,
                    messages,
                    AIToolContext(bot=bot, event=event, agent_config=cfg, auto=auto),
                ),
                timeout=request_timeout,
            )
        reply = strip_markdown(reply)
        from .reply import send_reply
        from .sticker import maybe_send_sticker

        sent = await send_reply(bot, event, reply, cfg)
        if sent:
            # 只有真正送到聊天里才写入短期/持久记忆，否则下一轮会凭空
            # 多出一条用户从未看到的 assistant 回复。
            remember(session, prompt_text, reply, cfg)
            remember_shared(event, text, reply, cfg)
            append_memory(event, cfg, text, reply)
            mark_activity(session)
            # 贴纸是锦上添花：文字都没发出去时不要再发一张空气贴纸。
            await maybe_send_sticker(bot, event, cfg, is_auto_reply=auto)
            if isinstance(event, GroupMessageEvent):
                auto_reply.note_bot_spoke(cfg, event.group_id)
            delivered = True
            stats_increment(event, "ai_chat_sessions", 1)
            # 检测并异步触发上一轮会话记忆自动总结（空闲 ≥10 分钟时）
            if should_summarize(session):
                asyncio.create_task(summarize_session_memory(cfg, event))
        else:
            logger.warning(
                "[AIAgent] 回复发送失败，不标记 delivered、退回预留额度 -> %s", session
            )
        mark_event_handled(event)
    except asyncio.TimeoutError:
        logger.warning("[AIAgent] 单次请求超过总预算 %.1fs，已取消 -> %s", request_timeout, session)
        if auto:
            return
        await bot.send(event, Message(msg("aiagent.timeout")))
        mark_event_handled(event)
    except AIAgentRequestError as e:
        logger.warning("[AIAgent] API 请求失败: %s", e)
        if auto:
            return
        if e.status_code in {401, 403}:
            await bot.send(event, Message(msg("aiagent.auth_failed")))
        elif e.status_code == 429:
            await bot.send(event, Message(msg("aiagent.rate_limited")))
        elif e.status_code >= 500:
            await bot.send(event, Message(msg("aiagent.upstream_error")))
        else:
            await bot.send(event, Message(msg("aiagent.failed")))
        mark_event_handled(event)
    except httpx.TimeoutException as e:
        logger.warning("[AIAgent] 请求超时: %s", e)
        if auto:
            return
        await bot.send(event, Message(msg("aiagent.timeout")))
        mark_event_handled(event)
    except httpx.HTTPError as e:
        logger.warning("[AIAgent] 网络请求失败: %s", e)
        if auto:
            return
        await bot.send(event, Message(msg("aiagent.network_error")))
        mark_event_handled(event)
    except Exception as e:
        logger.exception("[AIAgent] 聊天失败: %s", e)
        if auto:
            return
        await bot.send(event, Message(msg("aiagent.failed")))
        mark_event_handled(event)
    finally:
        # 没有成功回复（API 报错、网络异常、消息为空等）就退回预留的额度
        if quota_reserved and not delivered:
            refund_quota(cfg, event)


async def _handle_chat_event(bot: Bot, event: MessageEvent, text: str, *, auto: bool = False) -> None:
    session = session_key(event)
    async with _session_lock(session):
        if auto:
            await _handle_chat_event_unlocked(bot, event, text, auto=True)
        else:
            await _handle_chat_event_unlocked(bot, event, text)


def _is_directly_addressed(event: MessageEvent) -> bool:
    """是否被直接叫到：群里的 @ / 引用机器人 / 叫昵称，或私聊。

    OneBot 适配器会把「引用机器人」和「以机器人昵称开头」都折算成 `to_me`，
    所以这里只看 `is_tome()` 即可。
    """
    if isinstance(event, GroupMessageEvent):
        return event.is_tome()
    return True


def _session_lock(session: str) -> asyncio.Lock:
    lock = _session_locks.get(session)
    if lock is None:
        lock = asyncio.Lock()
        _session_locks[session] = lock
    return lock


aiagent_auto_matcher = on_message(priority=99, block=False)


@aiagent_auto_matcher.handle()
async def _handle_auto_chat(bot: Bot, event: MessageEvent) -> None:
    if is_command_handled(event):
        return
    # 不把自己的发言当成群友消息，避免自动回复自问自答。
    if str(event.get_user_id()) == str(getattr(event, "self_id", "")):
        return

    cfg = get_config_for_event(event)
    if not cfg.get("enabled", False):
        return
    # 黑白名单准入检查（优先于配额检查）
    if not is_event_allowed(cfg, event):
        logger.info(
            "[AIAgent] 黑白名单已阻止回复 -> user=%s group=%s",
            event.get_user_id(),
            getattr(event, "group_id", ""),
        )
        return

    direct = _is_directly_addressed(event)
    auto = False
    if not direct:
        if not isinstance(event, GroupMessageEvent):
            return
        if not auto_reply.allowed(cfg, event.group_id):
            return
        # 纯表情 / 纯语音 / 纯贴纸没有可回复内容，不占用冷却，避免把下一条
        # 正常文字消息也卡掉。
        if not event.get_plaintext().strip() and not collect_image_urls(event, cfg):
            return
        if not auto_reply.claim(cfg, event.group_id):
            logger.debug(
                "[AIAgent] 自动回复冷却中，跳过 -> group=%s user=%s",
                event.group_id,
                event.get_user_id(),
            )
            return
        auto = True
        logger.debug(
            "[AIAgent] 自动回复触发 -> group=%s user=%s",
            event.group_id,
            event.get_user_id(),
        )

    try:
        await _handle_chat_event(bot, event, event.get_plaintext(), auto=auto)
    finally:
        if auto:
            auto_reply.release(event.group_id)


# 多配置文件的管理命令（AI配置列表 / 切换AI配置 / 解绑AI配置）。
from . import profile_commands  # noqa: E402, F401 — 导入即注册命令


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
    cfg = get_config_for_event(ctx.event)
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
    cfg = get_config_for_event(ctx.event)
    if not cfg.get("enabled", False):
        await ctx.send(Message("AI Agent 未启用"))
        return

    await ctx.send(Message("⏳ 正在总结记忆，请稍候..."))
    result = await summarize_session_memory(cfg, ctx.event, force=True)
    await ctx.send(Message(result))


@command(
    "额度",
    aliases=("配额", "quota"),
    description="",
    usage="额度",
    show_in_help=False,
    require_tome=True,
)
async def handle_quota_query(ctx: CommandContext) -> None:
    """查询当前会话（群/个人）的 AI 配额使用情况。"""
    cfg = get_config_for_event(ctx.event)
    if not cfg.get("enabled", False):
        await ctx.send(Message("AI Agent 未启用"))
        return

    status = get_quota_status(cfg, ctx.event)
    if status["exempt"]:
        await ctx.send(Message(msg("aiagent.quota_exempt")))
        return
    if not status["enabled"]:
        await ctx.send(Message(msg("aiagent.quota_disabled")))
        return

    def _fmt(value: int) -> str:
        return "不限" if value < 0 else f"{value:,}"

    daily = status["daily"]
    hourly = status["hourly"]
    await ctx.send(
        Message(
            msg(
                "aiagent.quota_usage",
                daily_used=f"{daily['used']:,}",
                daily_limit=_fmt(daily["limit"]),
                daily_remaining=_fmt(daily["remaining"]),
                hourly_used=f"{hourly['used']:,}",
                hourly_limit=_fmt(hourly["limit"]),
                hourly_remaining=_fmt(hourly["remaining"]),
                daily_resets=daily["resets_at"],
                hourly_resets=hourly["resets_at"],
            )
        )
    )
