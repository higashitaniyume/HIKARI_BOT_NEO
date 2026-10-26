"""AI 回复里的 @ 标记渲染。

模型只能输出纯文本，不能直接产生消息段。这里约定一个纯文本标记
`[[at:QQ号]]`：模型用 `group_members` / `group_mention` 拿到 QQ 号后写进回复，
发送前由本模块替换成真正的 at 段，群友才会真的收到提醒。

设计约束：

- 标记只认 5-12 位数字 QQ 号，`@全体成员`（`all`）永远不会被渲染；
- 标记数量受 `tools.group_mention.max_per_reply` 限制，超出的标记直接丢弃；
- 私聊里没有 @ 的语义，标记会被去掉，不会泄漏成原始文本；
- 关闭 `tools.group_mention.enabled` 后标记同样只去掉、不渲染。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from .utils import safe_bool, safe_int

logger = logging.getLogger("HikariBot.AIAgent.Mention")

# 标记格式固定：QQ 号 5-12 位数字；不接受 all / 昵称。
# 允许模型写成 `[[at: 123456 ]]` / 全角冒号，尽量别让一个空格就失效。
MENTION_MARKER_RE = re.compile(
    r"\[\[\s*at\s*[:：]\s*(\d{5,12})\s*\]\]", re.IGNORECASE
)
# 模型写歪的标记（[[at:all]]、[[at:123]] 等）一律清掉，不让原始标记漏到聊天里；
# 超长回复被硬截断时可能留下没闭合的标记，也一并清掉。
_LEFTOVER_MARKER_RE = re.compile(r"\[\[\s*at\s*[:：][^\]]*\]\]", re.IGNORECASE)
_LEFTOVER_OPEN_RE = re.compile(r"\[\[\s*at\s*[:：][^\]]*$", re.IGNORECASE)


def _clean_text(value: str, *, enabled: bool) -> str:
    cleaned = (MENTION_MARKER_RE.sub("", value) if not enabled else value)
    cleaned = _LEFTOVER_MARKER_RE.sub("", cleaned)
    return _LEFTOVER_OPEN_RE.sub("", cleaned)

DEFAULT_MAX_PER_REPLY = 3
MAX_PER_REPLY_LIMIT = 10

# 一次切分结果：("text", 文本) 或 ("at", QQ 号)
MentionPart = tuple[str, str]


def _mention_cfg(cfg: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(cfg, dict):
        return {}
    tools = cfg.get("tools")
    if not isinstance(tools, dict):
        return {}
    section = tools.get("group_mention")
    return section if isinstance(section, dict) else {}


def mentions_enabled(cfg: dict[str, Any] | None) -> bool:
    return safe_bool(_mention_cfg(cfg).get("enabled"), True)


def max_mentions(cfg: dict[str, Any] | None) -> int:
    return safe_int(
        _mention_cfg(cfg).get("max_per_reply"),
        DEFAULT_MAX_PER_REPLY,
        minimum=0,
        maximum=MAX_PER_REPLY_LIMIT,
    )


def mention_marker(user_id: Any) -> str:
    """构造 @ 标记；`user_id` 不是数字时抛 ValueError。"""
    return f"[[at:{int(str(user_id).strip())}]]"


def has_mentions(text: str) -> bool:
    return bool(MENTION_MARKER_RE.search(str(text or "")))


def render_parts(text: str, *, enabled: bool, max_per_reply: int) -> list[MentionPart]:
    """把文本切成 text / at 片段；关闭或超额时只丢弃标记。"""
    raw = str(text or "")
    if not raw:
        return []
    if not enabled:
        cleaned = _clean_text(raw, enabled=False)
        return [("text", cleaned)] if cleaned else []

    parts: list[MentionPart] = []
    last = 0
    rendered = 0
    for match in MENTION_MARKER_RE.finditer(raw):
        if match.start() > last:
            parts.append(("text", raw[last : match.start()]))
        if rendered < max_per_reply:
            parts.append(("at", match.group(1)))
            rendered += 1
        last = match.end()
    if last < len(raw):
        parts.append(("text", raw[last:]))

    result: list[MentionPart] = []
    for kind, value in parts:
        if kind == "text":
            value = _clean_text(value, enabled=True)
            if not value:
                continue
        result.append((kind, value))
    return result


def build_message(
    text: str,
    cfg: dict[str, Any] | None = None,
    *,
    group: bool = True,
) -> Message:
    """构造带 @ 段的消息；私聊或关闭时标记只被去掉。"""
    enabled = bool(group) and mentions_enabled(cfg)
    parts = render_parts(
        text,
        enabled=enabled,
        max_per_reply=max_mentions(cfg) if enabled else 0,
    )
    message = Message()
    for kind, value in parts:
        if kind == "at":
            message += MessageSegment.at(int(value))
        else:
            message += MessageSegment.text(value)
    return message
