from __future__ import annotations

import re
from typing import Any


_MD_HEADING_RE = re.compile(r"^#{1,6}\s+")
_MD_BLOCKQUOTE_RE = re.compile(r"^>\s?")
_MD_UL_RE = re.compile(r"^[-*+]\s+")
_MD_OL_RE = re.compile(r"^\d{1,9}[.)]\s+")
_MD_HR_RE = re.compile(r"^[-*_]{3,}\s*$")
_MD_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*\n?([\s\S]*?)```\n?", re.MULTILINE)
_MD_FENCE_TILDE_RE = re.compile(r"~~~[a-zA-Z0-9_+-]*\n?([\s\S]*?)~~~\n?", re.MULTILINE)
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_REF_LINK_RE = re.compile(r"\[([^\]]+)\]\[[^\]]*\]")
_MD_REF_DEF_RE = re.compile(r"\n?\[[^\]]+\]:\s*\S+")
_MD_STRIKETHROUGH_RE = re.compile(r"~~([\s\S]*?)~~")
_MD_BOLD_ITALIC1_RE = re.compile(r"\*\*\*([\s\S]*?)\*\*\*")
_MD_BOLD_ITALIC2_RE = re.compile(r"___([\s\S]*?)___")
_MD_BOLD1_RE = re.compile(r"\*\*([\s\S]*?)\*\*")
_MD_BOLD2_RE = re.compile(r"__([\s\S]*?)__")
_MD_ITALIC_STAR_RE = re.compile(r"(?<!\w)\*([^*\n]+?)\*(?!\w)")
_MD_ITALIC_UNDERSCORE_RE = re.compile(r"(?<!\w)_([^_\n]+?)_(?!\w)")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")
# 清理可能泄漏的原始 tool_call 格式文本（DeepSeek V4 思考模式会在
# content 中输出 tool call 的 JSON/标记，需在 strip_markdown 前清除）
_TOOL_CALL_CLEANUP_RES = [
    re.compile(r"<function_calls>.*?</function_calls>", re.DOTALL),
    re.compile(r"<tool_calls>.*?</tool_calls>", re.DOTALL),
    re.compile(r"<thinking>.*?</thinking>", re.DOTALL),
    re.compile(r'{"tool_calls":.*?\]}', re.DOTALL),
    # DeepSeek V4 Flash 思考模式: robot-emoji + tool_calls + JSON 数组
    re.compile(r"\U0001F916tool_calls\s*\[[\s\S]*?\]", re.DOTALL),
    # 通用: emoji 前缀 + tool/function_call + JSON 内容残余
    re.compile(
        r"(?:(?<!\w)(?:tool|function)_calls?(?!\w))"
        r"[\s\S]{0,200}?(?:name|function|arguments)",
        re.DOTALL | re.IGNORECASE,
    ),
    re.compile(r"\[/?function[^\]]*\]", re.IGNORECASE),
    re.compile(r"\[/?tool[^\]]*\]", re.IGNORECASE),
    re.compile(r"Function call(?:s|):\s*\w+\([^)]*\)", re.IGNORECASE),
]


def strip_markdown(text: str) -> str:
    """Remove common Markdown formatting, keeping plain text content.

    Handles: headings, bold, italic, strikethrough, inline code, fenced code
    blocks, links, images, lists, blockquotes, and horizontal rules.
    """
    # 0. Clean up raw tool_call 格式文本（可能在思考模式下泄漏）
    for pattern in _TOOL_CALL_CLEANUP_RES:
        text = pattern.sub("", text)

    # 1. Fenced code blocks — must come first to avoid matching inside fences
    text = _MD_FENCE_RE.sub(r"\1", text)
    text = _MD_FENCE_TILDE_RE.sub(r"\1", text)

    # 2. Inline code
    text = re.sub(r"`([^`\n]+)`", r"\1", text)

    # 3. Images before links (same syntax with leading !)
    text = _MD_IMAGE_RE.sub(r"\1", text)

    # 4. Links: [text](url) and reference-style [text][ref]
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _MD_REF_LINK_RE.sub(r"\1", text)
    text = _MD_REF_DEF_RE.sub("", text)

    # 5. Strikethrough
    text = _MD_STRIKETHROUGH_RE.sub(r"\1", text)

    # 6. Bold+italic, bold, italic — outer-first to avoid partial matches
    text = _MD_BOLD_ITALIC1_RE.sub(r"\1", text)
    text = _MD_BOLD_ITALIC2_RE.sub(r"\1", text)
    text = _MD_BOLD1_RE.sub(r"\1", text)
    text = _MD_BOLD2_RE.sub(r"\1", text)
    text = _MD_ITALIC_STAR_RE.sub(r"\1", text)
    text = _MD_ITALIC_UNDERSCORE_RE.sub(r"\1", text)

    # 7. Line-by-line: headings, blockquotes, lists, HRs
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        raw = line.strip()
        raw = _MD_HEADING_RE.sub("", raw)
        raw = _MD_BLOCKQUOTE_RE.sub("", raw)
        raw = _MD_UL_RE.sub("", raw)
        raw = _MD_OL_RE.sub("", raw)
        if _MD_HR_RE.match(raw):
            cleaned.append("")
            continue
        cleaned.append(raw)

    text = "\n".join(cleaned)

    # 8. Collapse excessive blank lines
    text = _MULTI_BLANK_RE.sub("\n\n", text)

    return text.strip()


def normalize_text(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


def safe_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return min(max(parsed, minimum), maximum)


def safe_float(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        return default
    return min(max(parsed, minimum), maximum)


def safe_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def safe_id(value: Any) -> str:
    text = str(value or "").strip()
    return re.sub(r"[^A-Za-z0-9_-]", "_", text)[:80] or "unknown"
