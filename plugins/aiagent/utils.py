from __future__ import annotations

import json
import re
from typing import Any


# ── DSML (DeepSeek Markup Language) constants ───────────────────────────
# Fullwidth vertical bar U+FF5C used in DeepSeek V4 Flash native tool calls
_FW_VBAR = "｜"

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
    # DSML: <｜DSML｜tool_calls>...</｜DSML｜tool_calls> and nested tags
    re.compile(rf"<{_FW_VBAR}DSML{_FW_VBAR}tool_calls>.*?</{_FW_VBAR}DSML{_FW_VBAR}tool_calls>", re.DOTALL),
    re.compile(rf"<{_FW_VBAR}DSML{_FW_VBAR}invoke[^>]*>.*?</{_FW_VBAR}DSML{_FW_VBAR}invoke>", re.DOTALL),
    re.compile(rf"<{_FW_VBAR}DSML{_FW_VBAR}parameter[^>]*>.*?</{_FW_VBAR}DSML{_FW_VBAR}parameter>", re.DOTALL),
    re.compile(r"<function_calls>.*?</function_calls>", re.DOTALL),
    re.compile(r"<tool_calls>.*?</tool_calls>", re.DOTALL),
    re.compile(r"<thinking>.*?</thinking>", re.DOTALL),
    re.compile(r"<think>.*?</think>", re.DOTALL),
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


# ── DSML (DeepSeek Markup Language) Tool Call Parser ──────────────

# Regex patterns for DSML tool call format used by DeepSeek V4 Flash.
# Fullwidth vertical bar: ｜ (U+FF5C)
# Format:
#   <｜DSML｜tool_calls>
#     <｜DSML｜invoke name="tool_name">
#       <｜DSML｜parameter name="key" string="true">value</｜DSML｜parameter>
#     </｜DSML｜invoke>
#   </｜DSML｜tool_calls>
_DSML_TOOL_CALLS_RE = re.compile(
    rf"<{_FW_VBAR}DSML{_FW_VBAR}tool_calls>(.*?)</{_FW_VBAR}DSML{_FW_VBAR}tool_calls>",
    re.DOTALL,
)
_DSML_INVOKE_RE = re.compile(
    rf'<{_FW_VBAR}DSML{_FW_VBAR}invoke\s+name="([^"]*)">(.*?)</{_FW_VBAR}DSML{_FW_VBAR}invoke>',
    re.DOTALL,
)
_DSML_PARAM_RE = re.compile(
    rf'<{_FW_VBAR}DSML{_FW_VBAR}parameter\s+name="([^"]*)"\s+string="([^"]*)">(.*?)</{_FW_VBAR}DSML{_FW_VBAR}parameter>',
    re.DOTALL,
)


def parse_dsml_tool_calls(content: str) -> list[dict]:
    """Parse DeepSeek V4 Flash DSML tool calls from response content.

    Converts the native DSML (DeepSeek Markup Language) format into
    standard OpenAI-format tool call dicts so the existing tool execution
    loop can handle them.

    Returns:
        List of tool call dicts with keys:
            id, type, function.{name, arguments}
        Empty list if no DSML tool calls are found.
    """
    tool_calls: list[dict] = []
    for tc_match in _DSML_TOOL_CALLS_RE.finditer(content):
        tc_body = tc_match.group(1)
        for invoke_match in _DSML_INVOKE_RE.finditer(tc_body):
            tool_name = invoke_match.group(1).strip()
            invoke_body = invoke_match.group(2)

            arguments: dict[str, Any] = {}
            for param_match in _DSML_PARAM_RE.finditer(invoke_body):
                param_name = param_match.group(1).strip()
                is_string = param_match.group(2).strip() == "true"
                raw_value = param_match.group(3)

                if is_string:
                    arguments[param_name] = raw_value
                else:
                    try:
                        arguments[param_name] = json.loads(raw_value)
                    except json.JSONDecodeError:
                        arguments[param_name] = raw_value

            tool_calls.append({
                "id": f"call_dsml_{len(tool_calls)}",
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            })

    return tool_calls


def strip_dsml_tags(content: str) -> str:
    """Remove all DSML tool call markup from content, preserving any
    surrounding text (e.g. reasoning, partial replies)."""
    return _DSML_TOOL_CALLS_RE.sub("", content).strip()


def has_dsml_tool_calls(content: str) -> bool:
    """Return True if the content contains DSML tool call tags."""
    return bool(_DSML_TOOL_CALLS_RE.search(content))
