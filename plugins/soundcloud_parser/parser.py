"""
SoundCloud 链接提取模块。
"""

from __future__ import annotations

import html
import re

SOUNDCLOUD_URL_RE = re.compile(
    r"(?P<url>"
    # soundcloud.com/artist/title and subdomain variants
    r"(?:https?://)?(?:www\.|m\.|mobile\.)?soundcloud\.com/"
    r"[A-Za-z0-9_-]+/[A-Za-z0-9_-]+[^ \t\r\n<>，。！？；：]*"
    r"|"
    # on.soundcloud.com short links
    r"(?:https?://)?on\.soundcloud\.com/[A-Za-z0-9_-]+[^ \t\r\n<>，。！？；：]*"
    r")",
    re.IGNORECASE,
)

TRAILING_PUNCTUATION = ".,!?;:，。！？；：)]}>'\""


def extract_soundcloud_urls(text: str) -> list[str]:
    """从消息文本中提取 SoundCloud 链接，去重并保持顺序。"""
    urls: list[str] = []
    seen: set[str] = set()

    for match in SOUNDCLOUD_URL_RE.finditer(text):
        url = html.unescape(match.group("url")).rstrip(TRAILING_PUNCTUATION)
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)

    return urls
