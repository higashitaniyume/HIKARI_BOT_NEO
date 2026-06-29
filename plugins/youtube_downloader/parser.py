"""
YouTube 链接提取模块。
"""

from __future__ import annotations

import html
import re

YOUTUBE_URL_RE = re.compile(
    r"(?P<url>"
    r"(?:https?://)?(?:www\.|m\.|music\.)?youtube\.com/"
    r"(?:watch\?[^ \t\r\n<>，。！？；：]+|shorts/[A-Za-z0-9_-]{6,}[^ \t\r\n<>，。！？；：]*|"
    r"live/[A-Za-z0-9_-]{6,}[^ \t\r\n<>，。！？；：]*|embed/[A-Za-z0-9_-]{6,}[^ \t\r\n<>，。！？；：]*|"
    r"v/[A-Za-z0-9_-]{6,}[^ \t\r\n<>，。！？；：]*)"
    r"|(?:https?://)?(?:www\.)?youtu\.be/[A-Za-z0-9_-]{6,}[^ \t\r\n<>，。！？；：]*"
    r"|(?:https?://)?(?:www\.)?youtube-nocookie\.com/embed/[A-Za-z0-9_-]{6,}[^ \t\r\n<>，。！？；：]*"
    r")",
    re.IGNORECASE,
)

TRAILING_PUNCTUATION = ".,!?;:，。！？；：)]}>'\""


def extract_youtube_urls(text: str) -> list[str]:
    """从消息文本中提取 YouTube 链接，去重并保持顺序。"""
    urls: list[str] = []
    seen: set[str] = set()

    for match in YOUTUBE_URL_RE.finditer(text):
        url = html.unescape(match.group("url")).rstrip(TRAILING_PUNCTUATION)
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)

    return urls
