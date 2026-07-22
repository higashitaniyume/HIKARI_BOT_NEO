"""OneBot message conversion utilities — no NoneBot dependency at module level.

Separated from ``loader.py`` so tests can import conversion helpers
without triggering ``plugins.astrbot_compat.__init__`` (which calls
NoneBot's ``get_driver()``).
"""

from __future__ import annotations

from nonebot.adapters.onebot.v11 import MessageSegment

from astrbot.api.message_components import (
    BaseMessageComponent,
    Image,
    Json as JsonComp,
    Node as NodeComp,
    Plain,
    Record,
    Reply as ReplyComp,
    Share as ShareComp,
    Video,
)
from astrbot.core.message.message_event_result import MessageChain


def convert_chain_to_onebot(chain: MessageChain) -> str | list[MessageSegment]:
    """Convert a ``MessageChain`` to a OneBot-compatible message object."""
    segments: list[MessageSegment] = []

    for comp in chain.chain:
        seg = _component_to_segment(comp)
        if seg is not None:
            segments.append(seg)

    if not segments:
        return ""

    if len(segments) == 1 and segments[0].type == "text":
        return segments[0].data.get("text", "")

    return segments


def _component_to_segment(comp: BaseMessageComponent) -> MessageSegment | None:
    if isinstance(comp, Plain):
        return MessageSegment.text(comp.text)
    if isinstance(comp, Image):
        if comp.url:
            return MessageSegment.image(comp.url)
        if comp.file:
            return MessageSegment.image(comp.file)
        if comp.path:
            return MessageSegment.image(comp.path)
        return None
    if isinstance(comp, Record):
        url = comp.url or comp.file or comp.path
        if url:
            return MessageSegment.record(url)
        return None
    if isinstance(comp, Video):
        url = comp.url or comp.file
        if url:
            return MessageSegment.video(url)
        return None
    if isinstance(comp, ReplyComp):
        text = f"[回复 {comp.id}]"
        if comp.message_str:
            text += f" {comp.message_str}"
        elif comp.sender_nickname:
            text += f" ({comp.sender_nickname})"
        return MessageSegment.text(text)
    if isinstance(comp, ShareComp):
        text = f"🔗 {comp.title}: {comp.url}"
        return MessageSegment.text(text)
    if isinstance(comp, JsonComp) and comp.data:
        import json
        try:
            return MessageSegment.json(json.dumps(comp.data, ensure_ascii=False))
        except (TypeError, ValueError):
            return MessageSegment.text(str(comp.data))
    if isinstance(comp, NodeComp):
        texts = []
        for child in (comp.content or []):
            seg = _component_to_segment(child)
            if seg and seg.type == "text":
                texts.append(seg.data.get("text", ""))
        return MessageSegment.text("[转发消息] " + " | ".join(texts)) if texts else None
    if hasattr(comp, "text") and comp.text:
        return MessageSegment.text(str(comp.text))
    return None
