"""从 OneBot API 请求参数里抠出待审查的纯文本。

发送方可能传字符串（含 CQ 码）、Message、MessageSegment、纯 dict 段，或者合并
转发的 node 列表；node 的 content 里又是一层同样的东西。这里统一递归展平，只
保留 text 段和字符串里的可读文本——图片像素不看，但解析插件写进 text 段的标题
/ 作者 / 简介 / 热评都会被收进来。
"""

from __future__ import annotations

import re
from typing import Any

_TEXT_APIS = frozenset({"send_msg", "send_group_msg", "send_private_msg"})
_FORWARD_APIS = frozenset({"send_forward_msg", "send_group_forward_msg", "send_private_forward_msg"})

_CQ_PATTERN = re.compile(r"\[CQ:[^\]]*\]")
_MAX_DEPTH = 8


def api_kind(api: str) -> str:
    """返回 "text" / "forward" / ""（不是发消息的接口）。"""
    if api in _TEXT_APIS:
        return "text"
    if api in _FORWARD_APIS:
        return "forward"
    return ""


def payload_of(data: dict[str, Any], kind: str) -> Any:
    return data.get("messages") if kind == "forward" else data.get("message")


def extract_text(payload: Any) -> str:
    parts: list[str] = []
    _walk(payload, parts, 0)
    return "\n".join(parts)


def _walk(node: Any, parts: list[str], depth: int) -> None:
    if node is None or depth > _MAX_DEPTH:
        return
    if isinstance(node, str):
        _append(parts, _strip_cq(node))
        return
    if isinstance(node, (list, tuple, set)):
        for item in node:
            _walk(item, parts, depth + 1)
        return

    seg_type, seg_data = _segment_parts(node)
    if seg_type == "text":
        _append(parts, str(seg_data.get("text") or ""))
    elif seg_type in {"node", "forward"}:
        _walk(seg_data.get("content"), parts, depth + 1)


def _segment_parts(node: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(node, dict):
        raw_type = node.get("type")
        raw_data = node.get("data")
    else:
        raw_type = getattr(node, "type", None)
        raw_data = getattr(node, "data", None)
    if raw_type is None:
        return "", {}
    return str(raw_type or ""), raw_data if isinstance(raw_data, dict) else {}


def _append(parts: list[str], text: str) -> None:
    cleaned = text.strip()
    if cleaned:
        parts.append(cleaned)


def _strip_cq(raw: str) -> str:
    text = _CQ_PATTERN.sub(" ", raw)
    for escaped, plain in (("&#91;", "["), ("&#93;", "]"), ("&#44;", ","), ("&amp;", "&")):
        text = text.replace(escaped, plain)
    return text
