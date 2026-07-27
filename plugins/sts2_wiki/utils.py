"""
STS2 Wiki 文本处理工具函数。
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any


class _IntroTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._paragraph_depth = 0
        self._current: list[str] = []
        self.paragraphs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "table", "nav"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "p":
            self._paragraph_depth += 1
            self._current = []

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            if tag in {"script", "style", "table", "nav"}:
                self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "p" and self._paragraph_depth:
            text = _normalize_text("".join(self._current))
            if text:
                self.paragraphs.append(text)
            self._paragraph_depth -= 1
            self._current = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not self._paragraph_depth:
            return
        self._current.append(data)


def _coerce_mw_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        raw = value.get("*")
        return raw if isinstance(raw, str) else ""
    return ""


def _extract_intro_from_html(value: str) -> str:
    if not value:
        return ""
    parser = _IntroTextParser()
    parser.feed(value)
    return "\n\n".join(parser.paragraphs).strip()


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", value)


def _clean_wikitext(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    for _ in range(6):
        updated = re.sub(r"\{\{[^{}]*\}\}", " ", text, flags=re.DOTALL)
        if updated == text:
            break
        text = updated
    text = re.sub(r"'''?", "", text)
    text = re.sub(r"\[\[(?:[^|\]]+\|)?([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[https?://[^\s\]]+\s+([^\]]+)\]", r"\1", text)
    text = re.sub(r"={2,}[^=\n]+={2,}.*", "", text, flags=re.DOTALL)
    return _normalize_text(text)


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = html.unescape(value)
    text = re.sub(r"\[\s*\d+\s*\]", "", text)
    paragraphs = re.split(r"(?:\r?\n){2,}", text)
    lines: list[str] = []
    for paragraph in paragraphs:
        line = re.sub(r"\s+", " ", paragraph).strip()
        if line:
            lines.append(line)
    return "\n\n".join(lines)


def _first_paragraph(value: str) -> str:
    return value.strip().split("\n", 1)[0].strip()


def _truncate(value: str, max_chars: int) -> str:
    text = value.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"
