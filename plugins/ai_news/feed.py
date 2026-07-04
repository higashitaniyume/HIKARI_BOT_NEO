from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

import httpx

from core.bot_identity import format_bot_name_text

logger = logging.getLogger("HikariBot.AiNews.Feed")


class AiNewsFeedError(RuntimeError):
    """Raised when a configured AI news source cannot be fetched or parsed."""


@dataclass(frozen=True, slots=True)
class NewsSource:
    id: str
    title: str
    url: str
    group: str = "general"
    weight: float = 0.0


@dataclass(frozen=True, slots=True)
class NewsItem:
    source_id: str
    source_title: str
    source_group: str
    title: str
    link: str
    summary: str = ""
    published: datetime | None = None
    identity: str = ""
    weight: float = 0.0

    @property
    def key(self) -> str:
        raw = "|".join([self.source_id, self.identity or "", self.link or "", self.title or ""])
        return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()


async def fetch_source(source: NewsSource, config: dict[str, Any]) -> list[NewsItem]:
    timeout = _safe_float(config.get("timeout_seconds"), default=20.0, minimum=1.0, maximum=300.0)
    max_feed_bytes = _safe_int(config.get("max_feed_bytes"), default=2097152, minimum=65536, maximum=10485760)
    headers = {"User-Agent": format_bot_name_text(config.get("user_agent") or "{bot_name} AI News Reader")}
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
            response = await client.get(source.url)
            response.raise_for_status()
            content = response.content
    except httpx.HTTPError as e:
        raise AiNewsFeedError(f"{source.title} 拉取失败：{e}") from e

    if len(content) > max_feed_bytes:
        raise AiNewsFeedError(f"{source.title} 内容过大：{len(content)} bytes")

    return parse_feed_xml(content, source=source)


async def fetch_all_sources(sources: list[NewsSource], config: dict[str, Any]) -> list[NewsItem]:
    concurrency = _safe_int(config.get("fetch_concurrency"), default=4, minimum=1, maximum=16)
    semaphore = asyncio.Semaphore(concurrency)

    async def run(source: NewsSource) -> list[NewsItem]:
        async with semaphore:
            try:
                return await fetch_source(source, config)
            except AiNewsFeedError as e:
                logger.warning("[AiNews] 消息源 %s 读取失败: %s", source.id, e)
                return []
            except Exception as e:
                logger.exception("[AiNews] 消息源 %s 读取异常: %s", source.id, e)
                return []

    results = await asyncio.gather(*(run(source) for source in sources))
    items: list[NewsItem] = []
    for batch in results:
        items.extend(batch)
    return items


def parse_feed_xml(content: bytes | str, *, source: NewsSource) -> list[NewsItem]:
    raw = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)
    raw = raw.lstrip("\ufeff\r\n\t ")
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as e:
        raise AiNewsFeedError(f"{source.title} XML 解析失败：{e}") from e

    if _local_name(root.tag) == "feed":
        return _parse_atom(root, source=source)
    return _parse_rss(root, source=source)


def normalize_sources(config: dict[str, Any], options: dict[str, Any] | None = None) -> list[NewsSource]:
    options = options or {}
    allowed_ids = _option_set(options.get("source_ids") or options.get("sources"))
    allowed_groups = _option_set(options.get("groups") or options.get("group"))
    raw_sources = config.get("sources") if isinstance(config.get("sources"), list) else []
    result: list[NewsSource] = []
    seen: set[str] = set()
    for item in raw_sources:
        if not isinstance(item, dict) or not bool(item.get("enabled", True)):
            continue
        source_id = _safe_slug(item.get("id"))
        url = str(item.get("url") or "").strip()
        if not source_id or not _looks_like_url(url) or source_id in seen:
            continue
        group = str(item.get("group") or "general").strip() or "general"
        if allowed_ids and source_id.casefold() not in allowed_ids:
            continue
        if allowed_groups and group.casefold() not in allowed_groups:
            continue
        seen.add(source_id)
        result.append(
            NewsSource(
                id=source_id,
                title=str(item.get("title") or source_id).strip() or source_id,
                url=url,
                group=group,
                weight=_safe_float(item.get("weight"), default=0.0, minimum=-1000.0, maximum=1000.0),
            )
        )
    return result


def select_items(
    items: list[NewsItem],
    *,
    max_items: int,
    max_age_hours: int,
    now: datetime,
    keyword_boosts: list[str] | None = None,
    max_per_source: int = 0,
) -> list[NewsItem]:
    cutoff = now.astimezone(timezone.utc).timestamp() - max_age_hours * 3600 if max_age_hours > 0 else None
    filtered: list[NewsItem] = []
    for item in items:
        if cutoff is not None and item.published is not None and item.published.astimezone(timezone.utc).timestamp() < cutoff:
            continue
        filtered.append(item)

    deduped = _dedupe_items(filtered)
    boosts = [keyword.casefold() for keyword in keyword_boosts or [] if str(keyword).strip()]
    deduped.sort(key=lambda item: _score_item(item, now=now, keyword_boosts=boosts), reverse=True)
    if max_per_source <= 0:
        return deduped[:max_items]

    selected: list[NewsItem] = []
    counts: dict[str, int] = {}
    for item in deduped:
        count = counts.get(item.source_id, 0)
        if count >= max_per_source:
            continue
        selected.append(item)
        counts[item.source_id] = count + 1
        if len(selected) >= max_items:
            break
    return selected


def _parse_rss(root: ElementTree.Element, *, source: NewsSource) -> list[NewsItem]:
    channel = _first_child(root, "channel") or root
    return [
        item
        for item in (_parse_rss_item(node, source=source) for node in (_children(channel, "item") or _children(root, "item")))
        if item.title or item.link
    ]


def _parse_rss_item(node: ElementTree.Element, *, source: NewsSource) -> NewsItem:
    title = _clean_text(_text(node, "title") or "未命名资讯")
    link = _normalize_url(_text(node, "link"), base_url=source.url)
    identity = _text(node, "guid") or link or title
    summary = _clean_text(_text(node, "encoded") or _text(node, "description") or _text(node, "summary"))
    published = _parse_date(_text(node, "pubDate") or _text(node, "published") or _text(node, "updated") or _text(node, "date"))
    return NewsItem(
        source_id=source.id,
        source_title=source.title,
        source_group=source.group,
        title=title,
        link=link,
        summary=summary,
        published=published,
        identity=identity,
        weight=source.weight,
    )


def _parse_atom(root: ElementTree.Element, *, source: NewsSource) -> list[NewsItem]:
    return [item for item in (_parse_atom_entry(node, source=source) for node in _children(root, "entry")) if item.title or item.link]


def _parse_atom_entry(node: ElementTree.Element, *, source: NewsSource) -> NewsItem:
    title = _clean_text(_text(node, "title") or "未命名资讯")
    link = _normalize_url(_atom_link(node), base_url=source.url)
    identity = _text(node, "id") or link or title
    summary = _clean_text(_text(node, "summary") or _text(node, "content"))
    published = _parse_date(_text(node, "published") or _text(node, "updated"))
    return NewsItem(
        source_id=source.id,
        source_title=source.title,
        source_group=source.group,
        title=title,
        link=link,
        summary=summary,
        published=published,
        identity=identity,
        weight=source.weight,
    )


def _dedupe_items(items: list[NewsItem]) -> list[NewsItem]:
    best: dict[str, NewsItem] = {}
    for item in items:
        keys = [_link_fingerprint(item.link), _title_fingerprint(item.title)]
        keys = [key for key in keys if key]
        if not keys:
            keys = [item.key]
        existing = next((best[key] for key in keys if key in best), None)
        if existing is None:
            for key in keys:
                best[key] = item
            continue
        chosen = _choose_better(existing, item)
        for key in keys + [_link_fingerprint(existing.link), _title_fingerprint(existing.title)]:
            if key:
                best[key] = chosen
    unique: dict[str, NewsItem] = {}
    for item in best.values():
        unique[item.key] = item
    return list(unique.values())


def _choose_better(left: NewsItem, right: NewsItem) -> NewsItem:
    left_time = left.published.timestamp() if left.published else 0
    right_time = right.published.timestamp() if right.published else 0
    left_score = left.weight + left_time / 100000000
    right_score = right.weight + right_time / 100000000
    return right if right_score > left_score else left


def _score_item(item: NewsItem, *, now: datetime, keyword_boosts: list[str]) -> float:
    score = item.weight
    text = f"{item.title} {item.summary}".casefold()
    score += sum(10.0 for keyword in keyword_boosts if keyword and keyword in text)
    if item.published is not None:
        age_hours = max(0.0, (now.astimezone(timezone.utc) - item.published.astimezone(timezone.utc)).total_seconds() / 3600)
        score += max(0.0, 168.0 - min(age_hours, 168.0))
    return score


def _atom_link(node: ElementTree.Element) -> str:
    fallback = ""
    for child in _children(node, "link"):
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


def _parse_date(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _link_fingerprint(link: str) -> str:
    if not link:
        return ""
    parts = urlsplit(link)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in {"ref", "fbclid", "gclid"}
    ]
    normalized = urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), parts.path.rstrip("/"), urlencode(query), ""))
    return normalized or ""


def _title_fingerprint(title: str) -> str:
    text = re.sub(r"\s+", " ", str(title or "").casefold()).strip()
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "", text)
    return text[:160]


def _option_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, list):
        return {str(item).strip().casefold() for item in value if str(item).strip()}
    return {part.strip().casefold() for part in str(value).split(",") if part.strip()}


def _looks_like_url(value: str) -> bool:
    parts = urlsplit(str(value or "").strip())
    return parts.scheme in {"http", "https"} and bool(parts.netloc)


def _safe_slug(value: Any) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", text):
        return ""
    return text


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
