from __future__ import annotations

import hashlib
import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin
from xml.etree import ElementTree

import httpx

from core.bot_identity import format_bot_name_text

logger = logging.getLogger("HikariBot.RssSubscriber.Feed")


class RssFeedError(RuntimeError):
    """Raised when an RSS or Atom feed cannot be fetched or parsed."""


@dataclass(frozen=True, slots=True)
class RssEntry:
    title: str
    link: str
    summary: str = ""
    published: str = ""
    identity: str = ""

    @property
    def key(self) -> str:
        raw = self.identity or self.link or self.title
        return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()


@dataclass(frozen=True, slots=True)
class RssFeed:
    title: str
    url: str
    entries: list[RssEntry]


async def fetch_feed(url: str, config: dict[str, Any]) -> RssFeed:
    timeout = _safe_float(config.get("timeout_seconds"), default=20.0, minimum=1.0, maximum=300.0)
    max_feed_bytes = _safe_int(config.get("max_feed_bytes"), default=2097152, minimum=65536, maximum=10485760)
    headers = {"User-Agent": format_bot_name_text(config.get("user_agent") or "{bot_name} RSS Reader")}
    client_kwargs: dict[str, Any] = {
        "timeout": httpx.Timeout(timeout),
        "headers": headers,
        "follow_redirects": True,
    }
    proxy = str(config.get("proxy") or "").strip()
    if proxy:
        client_kwargs["proxy"] = proxy

    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            response = await client.get(url)
            response.raise_for_status()
            content = response.content
    except httpx.HTTPError as e:
        raise RssFeedError(f"RSS 拉取失败：{e}") from e

    if len(content) > max_feed_bytes:
        raise RssFeedError(f"RSS 内容过大：{len(content)} bytes")

    return parse_feed_xml(content, url=url)


def parse_feed_xml(content: bytes | str, *, url: str = "") -> RssFeed:
    raw = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)
    raw = raw.lstrip("\ufeff\r\n\t ")
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as e:
        raise RssFeedError(f"RSS XML 解析失败：{e}") from e

    root_name = _local_name(root.tag)
    if root_name == "feed":
        return _parse_atom(root, url=url)
    return _parse_rss(root, url=url)


def format_feed_message(
    feed: RssFeed,
    entries: list[RssEntry],
    *,
    include_summary: bool,
    summary_max_chars: int,
    max_message_chars: int,
) -> str:
    if not entries:
        return ""

    title = feed.title or "RSS 订阅"
    lines = [f"{title} 更新："]
    for index, entry in enumerate(entries, start=1):
        item_lines = [f"{index}. {entry.title or '未命名条目'}"]
        if entry.published:
            item_lines.append(f"时间：{entry.published}")
        if include_summary and entry.summary and summary_max_chars > 0:
            item_lines.append(_truncate(entry.summary, summary_max_chars))
        if entry.link:
            item_lines.append(entry.link)
        lines.append("\n".join(item_lines))

    return _truncate("\n\n".join(lines), max_message_chars)


def _parse_rss(root: ElementTree.Element, *, url: str) -> RssFeed:
    channel = _first_child(root, "channel")
    if channel is None:
        channel = root
    feed_title = _text(channel, "title") or "RSS 订阅"
    item_nodes = _children(channel, "item") or _children(root, "item")
    entries = [_parse_rss_item(item, base_url=url) for item in item_nodes]
    entries = [entry for entry in entries if entry.title or entry.link]
    return RssFeed(title=_clean_text(feed_title), url=url, entries=entries)


def _parse_rss_item(item: ElementTree.Element, *, base_url: str) -> RssEntry:
    title = _clean_text(_text(item, "title") or "未命名条目")
    link = _normalize_url(_text(item, "link"), base_url=base_url)
    identity = _text(item, "guid") or link or title
    summary = _clean_text(_text(item, "encoded") or _text(item, "description") or _text(item, "summary"))
    published = _format_date(_text(item, "pubDate") or _text(item, "published") or _text(item, "date"))
    return RssEntry(title=title, link=link, summary=summary, published=published, identity=identity)


def _parse_atom(root: ElementTree.Element, *, url: str) -> RssFeed:
    feed_title = _clean_text(_text(root, "title") or "RSS 订阅")
    entries = [_parse_atom_entry(item, base_url=url) for item in _children(root, "entry")]
    entries = [entry for entry in entries if entry.title or entry.link]
    return RssFeed(title=feed_title, url=url, entries=entries)


def _parse_atom_entry(item: ElementTree.Element, *, base_url: str) -> RssEntry:
    title = _clean_text(_text(item, "title") or "未命名条目")
    link = _normalize_url(_atom_link(item), base_url=base_url)
    identity = _text(item, "id") or link or title
    summary = _clean_text(_text(item, "summary") or _text(item, "content"))
    published = _format_date(_text(item, "published") or _text(item, "updated"))
    return RssEntry(title=title, link=link, summary=summary, published=published, identity=identity)


def _atom_link(item: ElementTree.Element) -> str:
    fallback = ""
    for child in _children(item, "link"):
        href = str(child.attrib.get("href") or "").strip()
        if not href:
            continue
        rel = str(child.attrib.get("rel") or "alternate").strip().casefold()
        if rel == "alternate":
            return href
        if not fallback:
            fallback = href
    return fallback


def _children(parent: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [child for child in list(parent) if _local_name(child.tag) == name]


def _first_child(parent: ElementTree.Element, name: str) -> ElementTree.Element | None:
    for child in list(parent):
        if _local_name(child.tag) == name:
            return child
    return None


def _text(parent: ElementTree.Element, name: str) -> str:
    child = _first_child(parent, name)
    if child is None:
        return ""
    return "".join(child.itertext()).strip()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _clean_text(value: str) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _normalize_url(value: str, *, base_url: str) -> str:
    link = str(value or "").strip()
    if not link:
        return ""
    return urljoin(base_url, html.unescape(link))


def _format_date(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return raw
    return parsed.isoformat(timespec="minutes")


def _truncate(value: str, max_chars: int) -> str:
    text = str(value or "")
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    if max_chars <= 1:
        return text[:max_chars]
    return f"{text[: max_chars - 1]}…"


def _safe_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return min(max(parsed, minimum), maximum)


def _safe_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        return default
    return min(max(parsed, minimum), maximum)
