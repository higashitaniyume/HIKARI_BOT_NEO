"""AI Agent 回复发送与智能切分模块。

策略：
- 当回复长度 ≤ `single_message_chars` 时，以普通消息（支持引用）发送；
- 当回复超过单条阈值时，按自然的段落/句子边界切分，全部分段放进一个合并聊天记录（群聊 / 私聊均支持）；
- 合并转发失败时，优雅降级为逐条普通消息发送（段间短暂延迟），不丢弃内容；
- 节点数超限时优先合并短段，仍超则在最后节点追加截断提示。
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment

from core.bot_identity import get_bot_name
from .utils import safe_bool, safe_int

logger = logging.getLogger("HikariBot.AIAgent.Reply")

_SENTENCE_ENDINGS = ("。", "！", "？", "；", "…", "!", "?", ";")
_PAUSE_PUNCTUATION = ("，", ",", "、", "：", ":")
_URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)

# NapCat / QQ 偶发不回应 send_msg 时，bot.send 会一直挂住；AI 处理器持有
# 会话锁，挂住一次就会让该会话后续所有消息永远排队。给发送加统一超时，
# 超时只放弃当次发送并记录 warning，不阻塞会话锁。
SEND_TIMEOUT_SECONDS = 30.0
# 明确的发送失败（ActionFailed / 网络抖动）重试一次；超时不重试，
# 因为超时只代表「没等到结果」，NapCat 可能仍在后台上传并最终送达。
SEND_RETRY_DELAY_SECONDS = 1.0


async def _send_once(
    bot: Any,
    event: MessageEvent,
    message: Message,
    timeout: float,
) -> None:
    await asyncio.wait_for(bot.send(event, message), timeout=timeout)


async def send_with_timeout(
    bot: Any,
    event: MessageEvent,
    message: Message,
    timeout_seconds: float | None = None,
) -> bool:
    """发送消息并施加超时；返回是否成功，不向上抛发送异常。

    超时不重试；明确的 ActionFailed 等失败会短暂等待后重试一次，
    避免 NapCat/QQ 偶发 result 120 直接吞掉整条回复。
    """
    timeout = SEND_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    try:
        await _send_once(bot, event, message, timeout)
        return True
    except asyncio.TimeoutError:
        logger.warning("[AIAgent] 发送消息超时 %.1fs，放弃本次发送", timeout)
        return False
    except Exception as e:
        logger.warning("[AIAgent] 发送消息失败，%.1fs 后重试一次: %s", SEND_RETRY_DELAY_SECONDS, e)
        await asyncio.sleep(SEND_RETRY_DELAY_SECONDS)
        try:
            await _send_once(bot, event, message, timeout)
            return True
        except asyncio.TimeoutError:
            logger.warning("[AIAgent] 发送消息重试超时 %.1fs，放弃本次发送", timeout)
            return False
        except Exception as retry_error:
            logger.warning("[AIAgent] 发送消息重试失败: %s", retry_error)
            return False


def _quote_reply_enabled(chat_cfg: dict[str, Any]) -> bool:
    return safe_bool(chat_cfg.get("quote_reply"), True)


def build_reply_message(event: MessageEvent, text: str, cfg: dict[str, Any]) -> Message:
    """构建单条普通回复消息，按需添加引用。"""
    chat_cfg = cfg.get("chat") if isinstance(cfg.get("chat"), dict) else {}
    msg = Message(text)
    if not _quote_reply_enabled(chat_cfg):
        return msg
    message_id = getattr(event, "message_id", None)
    if message_id is None or not str(message_id).strip():
        return msg
    return Message(MessageSegment.reply(message_id)) + msg


def _find_best_cut(text: str, max_chars: int) -> int:
    """在 text[:max_chars] 中按自然语言语义寻找最佳切分点。

    优先级：
    1. 空行 / 段落 (\n\n)
    2. 换行 (\n)
    3. 句末标点 (。！？；…!?;)
    4. 停顿逗号冒号 (，,、：:)
    5. 空格 ( )
    6. 硬切
    避免从 URL 中间截断。
    """
    if len(text) <= max_chars:
        return len(text)

    # 检查 max_chars 是否刚好落在一个 URL 中间
    for m in _URL_PATTERN.finditer(text):
        if m.start() < max_chars < m.end():
            if m.start() > 0:
                before_url = text[: m.start()].rstrip()
                if before_url:
                    return len(before_url)
            else:
                # URL 在开头且比 max_chars 长：尽量保留完整 URL
                return m.end()

    candidate_text = text[:max_chars]
    min_cut = max(1, int(len(candidate_text) * 0.3))

    # 1. 段落边界 (\n\n)
    pos = candidate_text.rfind("\n\n")
    if pos >= min_cut:
        return pos + 2

    # 2. 换行 (\n)
    pos = candidate_text.rfind("\n")
    if pos >= min_cut:
        return pos + 1

    # 3. 句末标点
    best_punct = -1
    for p in _SENTENCE_ENDINGS:
        pos = candidate_text.rfind(p)
        if pos >= min_cut and pos + len(p) > best_punct:
            best_punct = pos + len(p)
    # 处理英文句号（后面带空格或换行）
    for match in re.finditer(r"\.\s", candidate_text):
        if match.start() >= min_cut and match.end() > best_punct:
            best_punct = match.end()
    if best_punct > 0:
        return best_punct

    # 4. 逗号 / 顿号 / 冒号
    best_pause = -1
    for p in _PAUSE_PUNCTUATION:
        pos = candidate_text.rfind(p)
        if pos >= min_cut and pos + len(p) > best_pause:
            best_pause = pos + len(p)
    if best_pause > 0:
        return best_pause

    # 5. 空格
    space_pos = candidate_text.rfind(" ")
    if space_pos >= min_cut:
        return space_pos + 1

    # 6. 硬切
    return max_chars


def split_reply(text: str, node_chars: int = 1500, max_nodes: int = 30) -> list[str]:
    """把回复切分为若干段落，每段尽量保持段落/句子完整。"""
    raw = text.strip()
    if not raw:
        return []
    if len(raw) <= node_chars:
        return [raw]

    segments: list[str] = []
    remaining = raw

    while remaining:
        if len(remaining) <= node_chars:
            segments.append(remaining.strip())
            break
        cut = _find_best_cut(remaining, node_chars)
        part = remaining[:cut].strip()
        if part:
            segments.append(part)
        remaining = remaining[cut:].lstrip()

    # 如果分段数超过上限，尝试合并相邻较短段落
    if len(segments) > max_nodes:
        merged: list[str] = []
        curr = ""
        for seg in segments:
            if not curr:
                curr = seg
            elif len(curr) + len(seg) + 2 <= node_chars:
                curr = f"{curr}\n\n{seg}"
            else:
                merged.append(curr)
                curr = seg
        if curr:
            merged.append(curr)
        segments = merged

    # 如果仍然超过上限，则截断到 max_nodes 并在末尾追加提示
    if len(segments) > max_nodes:
        segments = segments[:max_nodes]
        last = segments[-1]
        suffix = "\n\n（内容过长已截断）"
        if len(last) + len(suffix) <= node_chars:
            segments[-1] = f"{last}{suffix}"
        else:
            segments[-1] = f"{last[: node_chars - len(suffix)]}{suffix}"

    return [s for s in segments if s.strip()]


async def send_reply(
    bot: Bot,
    event: MessageEvent,
    reply: str,
    cfg: dict[str, Any],
) -> bool:
    """根据长度决定发送普通消息还是合并转发。

    返回是否至少成功送出一部分内容。调用方据此决定是否标记 delivered /
    退回配额 / 继续发送跟随贴纸，避免发送失败被静默吞掉。
    """
    chat_cfg = cfg.get("chat") if isinstance(cfg.get("chat"), dict) else {}
    single_message_chars = safe_int(
        chat_cfg.get("single_message_chars"),
        safe_int(chat_cfg.get("short_reply_chars"), 1200, minimum=0, maximum=12000),
        minimum=0,
        maximum=12000,
    )
    max_reply_chars = safe_int(chat_cfg.get("max_reply_chars"), 8000, minimum=10, maximum=30000)
    forward_node_chars = safe_int(chat_cfg.get("forward_node_chars"), 1500, minimum=10, maximum=5000)
    forward_max_nodes = safe_int(chat_cfg.get("forward_max_nodes"), 30, minimum=1, maximum=100)

    # 1. 短回复：普通消息（支持引用）
    if single_message_chars > 0 and len(reply) <= single_message_chars:
        return await send_with_timeout(bot, event, build_reply_message(event, reply, cfg))

    # 2. 长回复：智能分段合并转发
    trimmed_reply = reply
    if len(reply) > max_reply_chars:
        trimmed_reply = f"{reply[:max_reply_chars]}\n\n（内容过长已截断）"

    nodes_text = split_reply(trimmed_reply, node_chars=forward_node_chars, max_nodes=forward_max_nodes)
    if not nodes_text:
        return False

    bot_name = get_bot_name()
    bot_uid = int(bot.self_id)
    nodes = [
        MessageSegment.node_custom(
            user_id=bot_uid,
            nickname=bot_name,
            content=Message(seg_text),
        )
        for seg_text in nodes_text
    ]

    group_id = getattr(event, "group_id", None)
    try:
        if group_id is not None or isinstance(event, GroupMessageEvent):
            await asyncio.wait_for(
                bot.call_api("send_group_forward_msg", group_id=int(group_id), messages=nodes),
                timeout=SEND_TIMEOUT_SECONDS,
            )
        else:
            user_id = getattr(event, "user_id", None)
            if user_id is None:
                user_id = event.get_user_id()
            await asyncio.wait_for(
                bot.call_api("send_private_forward_msg", user_id=int(user_id), messages=nodes),
                timeout=SEND_TIMEOUT_SECONDS,
            )
        return True
    except asyncio.TimeoutError:
        # 合并转发挂住时不要继续逐条重试：每一条都可能再挂 30 秒，
        # 会话锁会被占得更久，直接放弃本次发送。
        logger.warning("[AIAgent] 合并转发发送超时 %.1fs，放弃本次发送", SEND_TIMEOUT_SECONDS)
        return False
    except Exception as e:
        logger.warning("[AIAgent] 合并转发发送失败，降级为逐条普通消息发送: %s", e)
        sent_any = False
        for seg_text in nodes_text:
            if not await send_with_timeout(bot, event, Message(seg_text)):
                break
            sent_any = True
            await asyncio.sleep(0.3)
        return sent_any
