"""Parse preparation and retry logic for the aggregated media parser."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

from nonebot.adapters.onebot.v11 import Bot, MessageEvent, Message

from core.bot_messages import get_message as msg
from core.activity_tracker import ActivityScope
from third_party.astrbot_plugin_media_parser.core.parser.utils import extract_url_from_card_data
from third_party.astrbot_plugin_media_parser.core.storage.parse_record import ParseRecordManager

from .cache_cleanup import media_cache_ttl_seconds, register_metadata_temp_media
from .config import get_config, video_only_platforms
from .runtime import MediaParserRuntime
from .queues import (
    MediaParseQueueItem,
    MediaSendQueueItem,
    MediaPrepareAttempt,
    _enqueue_send,
    _get_runtime,
    _get_session,
    _retry_settings,
    _create_record_manager,
    _trigger_bilibili_cookie_assist_if_needed,
)

logger = logging.getLogger("HikariBot.MediaParser")


def is_platform_allowed(platform: str, event: MessageEvent) -> bool:
    cfg = get_config()
    permissions_dict = cfg.get("permissions", {})
    if not isinstance(permissions_dict, dict):
        permissions_dict = {}

    platform_permissions = None
    if platform in permissions_dict:
        val = permissions_dict[platform]
        if isinstance(val, dict) and (val.get("whitelist", {}).get("enable") or val.get("blacklist", {}).get("enable") or val.get("admin_id")):
            platform_permissions = val

    if platform_permissions is None:
        if "whitelist" in permissions_dict or "blacklist" in permissions_dict:
            platform_permissions = permissions_dict
        else:
            platform_permissions = {}

    mock_config = {"permissions": platform_permissions}
    from core.access_control import is_event_allowed
    return is_event_allowed(mock_config, event)


def normalize_link_url(url: str) -> str:
    """还原链接里的转义：CQ 码序列化会把 `&` 写成 `&amp;`、`,` 写成 `&#44;`。

    这既用于去重（转义/未转义是同一链接），也必须在真正发起解析前生效：
    带 `&amp;` 的地址请求出去后，`xsec_token` 会被当成 `amp;xsec_token`，
    小红书这类依赖签名参数的平台会直接报"无法获取作品信息"。
    """
    return html.unescape(url or "").strip()


# 受支持平台的域名标记（含各平台短链域名）。
# handler 触发粗筛与 QQ 卡片链接过滤共用这一份，避免两处各写一份导致漏配（例如 xhslink.cn）。
SUPPORTED_LINK_MARKERS = (
    "bilibili.com",
    "b23.tv",
    "douyin.com",
    "iesdouyin.com",
    "tiktok.com",
    "kuaishou.com",
    "gifshow.com",
    "chenzhongtech.com",
    "weibo.com",
    "weibo.cn",
    "xiaohongshu.com",
    "xhslink.com",
    "xhslink.cn",
    "goofish.com",
    "m.tb.cn",
    "toutiao.com",
    "xiaoheihe.cn",
    "steampowered.com",
    "twitter.com",
    "x.com",
)

_URL_IN_TEXT_RE = re.compile(r"https?://[^\s\"'<>\\]+", re.IGNORECASE)
_URL_TRAILING_CHARS = ".,;:!?、，。！？；）】》”’>"
_CARD_SCAN_MAX_DEPTH = 6


def text_has_supported_link(text: str) -> bool:
    """粗筛：文本里是否出现受支持平台的域名标记（转义后的卡片文本也能命中）。"""
    lowered = html.unescape(text or "").casefold()
    return any(marker in lowered for marker in SUPPORTED_LINK_MARKERS)


def is_supported_platform_url(url: str) -> bool:
    """按主机名判断 URL 是否属于受支持平台（含子域名）。"""
    try:
        host = (urlparse(normalize_link_url(url)).hostname or "").lower().rstrip(".")
    except (TypeError, ValueError):
        return False
    if not host:
        return False
    return any(
        host == marker or host.endswith(f".{marker}")
        for marker in SUPPORTED_LINK_MARKERS
    )


def dedupe_links(links: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
    """去重并归一化链接：同一链接的转义/未转义形态只保留一条，且统一输出未转义地址。

    归一化后的地址会直接进入解析队列，因此这里必须返回 `normalize_link_url` 的结果，
    否则 QQ 卡片/CQ 码文本里的 `&amp;` 会被原样请求出去。
    """
    seen: set[str] = set()
    result: list[tuple[str, Any]] = []
    for url, parser in links:
        normalized = normalize_link_url(url)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append((normalized, parser))
    return result


def _event_text(event: MessageEvent) -> str:
    parts = [str(event.get_message())]
    plain = event.get_plaintext()
    if plain and plain not in parts[0]:
        parts.append(plain)
    parts.extend(_extract_card_urls(event))
    return "\n".join(part for part in parts if part)


def _extract_card_urls(event: MessageEvent) -> list[str]:
    """Extract URLs hidden in OneBot JSON/XML card segments."""
    urls: list[str] = []
    seen: set[str] = set()
    for segment in event.get_message():
        if getattr(segment, "type", "") == "text":
            continue
        data = getattr(segment, "data", None)
        if data is None:
            continue
        for candidate in _card_url_candidates(data):
            key = normalize_link_url(candidate)
            if candidate and key not in seen:
                seen.add(key)
                urls.append(candidate)
    if urls:
        logger.debug(
            "[MediaParser] 从 QQ 卡片提取到 %d 个平台链接: %s",
            len(urls),
            [url[:80] for url in urls],
        )
    return urls


def _card_url_candidates(data: Any) -> list[str]:
    """从单个消息段的 data 中提取受支持平台的链接。

    QQ 卡片的 data 形态不一：已解析 dict、`{"data": "{...json...}"}` 嵌套、纯 JSON/XML 字符串。
    vendored 提取器只认 `meta.detail_1.qqdocurl` 与 `meta.news.jumpUrl`，
    而小红书等小程序卡片的跳转地址可能落在 `detail_1.url`、`music.jumpUrl` 等字段上，
    因此在优先字段之后再对整张卡片做一次平台域名扫描兜底。
    """
    candidates: list[str] = []

    def add(url: Any) -> None:
        if not isinstance(url, str):
            return
        cleaned = normalize_link_url(url).rstrip(_URL_TRAILING_CHARS)
        if cleaned and is_supported_platform_url(cleaned):
            candidates.append(cleaned)

    add(extract_url_from_card_data(data))
    if isinstance(data, dict):
        for value in data.values():
            add(extract_url_from_card_data(value))

    for url in _scan_card_urls(data):
        add(url)

    return candidates


def _scan_card_urls(value: Any, depth: int = 0) -> list[str]:
    """递归扫描卡片结构中的所有 http(s) 链接（含嵌套 JSON 字符串与 `\\/` 转义）。"""
    if depth > _CARD_SCAN_MAX_DEPTH:
        return []

    found: list[str] = []
    if isinstance(value, str):
        text = html.unescape(value).replace("\\/", "/")
        found.extend(match.group(0) for match in _URL_IN_TEXT_RE.finditer(text))
        nested = _loads_json_container(value)
        if nested is not None:
            found.extend(_scan_card_urls(nested, depth + 1))
    elif isinstance(value, dict):
        for item in value.values():
            found.extend(_scan_card_urls(item, depth + 1))
    elif isinstance(value, list):
        for item in value:
            found.extend(_scan_card_urls(item, depth + 1))
    return found


def _loads_json_container(raw: str) -> Any:
    """把卡片里嵌套的 JSON 字符串解析成 dict/list，失败时返回 None。"""
    text = raw.strip()
    if not text.startswith(("{", "[")):
        return None
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def _is_video_only(runtime: MediaParserRuntime, metadata: dict[str, Any]) -> bool:
    """按 metadata 的平台名判断该平台是否配置成「仅视频」。"""
    platforms = video_only_platforms(runtime.config)
    if not platforms:
        return False
    for key in ("platform", "parser_name"):
        name = str(metadata.get(key) or "").strip()
        if name and name in platforms:
            return True
    return False


def _apply_output_modes(runtime: MediaParserRuntime, metadata: dict[str, Any]) -> bool:
    text_enabled, rich_enabled = runtime.config_manager.parser_output.output_for_metadata(metadata)
    video_only = _is_video_only(runtime, metadata)
    metadata["_enable_text_metadata"] = text_enabled
    metadata["_enable_rich_media"] = rich_enabled
    metadata["_video_only"] = video_only
    if metadata.get("error"):
        return text_enabled
    if video_only:
        # 「仅视频」：只发送解析出的视频；没有视频时保留条目，由发送链提示用户。
        if not metadata.get("video_urls"):
            logger.info(
                "[MediaParser] video-only platform without video -> platform=%s url=%s",
                metadata.get("platform") or metadata.get("parser_name") or "unknown",
                metadata.get("source_url") or metadata.get("url") or "",
            )
        return rich_enabled
    if rich_enabled and (metadata.get("video_urls") or metadata.get("image_urls")):
        return True
    if text_enabled:
        return bool(
            metadata.get("title")
            or metadata.get("author")
            or metadata.get("desc")
            or metadata.get("access_message")
            or metadata.get("source_url")
        )
    return False


async def _process_parse_item(item: MediaParseQueueItem) -> None:
    result = await _prepare_text(
        item.bot,
        item.event,
        item.text,
        force=item.force,
        links_with_parser=item.links_with_parser,
    )
    if result is None:
        return
    processed, config = result
    await _enqueue_send(MediaSendQueueItem(
        bot=item.bot,
        event=item.event,
        processed=processed,
        config=config,
        force=item.force,
    ))


async def _prepare_text(
    bot: Bot,
    event: MessageEvent,
    text: str,
    *,
    force: bool = False,
    links_with_parser: list[tuple[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    runtime = _get_runtime()
    if runtime is None:
        return None
    links = list(links_with_parser) if links_with_parser is not None else runtime.parser_manager.extract_all_links(text)
    links = dedupe_links(links)
    links = [
        (url, parser) for url, parser in links
        if is_platform_allowed(getattr(parser, "name", "unknown"), event)
    ]
    if not links:
        if force:
            await bot.send(event, Message(msg("media_parser.no_link")))
        return None

    if not force and not runtime.config_manager.trigger.should_parse(text):
        return None

    if links_with_parser is None:
        max_links = max(1, int(runtime.config.get("max_links_per_message", 20)))
        links = links[:max_links]
    record_manager = _create_record_manager(runtime)
    if record_manager.enabled:
        links, blocked = record_manager.filter_links(
            links,
            user_key=ParseRecordManager.build_user_key("onebot", event.get_user_id()),
        )
        if blocked and force:
            await bot.send(event, Message(msg("media_parser.rate_limited", reason=blocked[0].reason)))
        if not links:
            return None
    logger.info(
        "[MediaParser] parse triggered -> user=%s, links=%d",
        event.get_user_id(),
        len(links),
    )

    result = await _prepare_links_with_retries(bot, event, text, links, initial_config=runtime.config)
    if result is None:
        return None
    if record_manager.enabled:
        record_manager.record_metadata_links(result.metadata_list)
    if not result.processed:
        if force:
            await bot.send(event, Message(msg("media_parser.empty")))
        return None
    return result.processed, result.config


async def _prepare_links_with_retries(
    bot: Bot,
    event: MessageEvent,
    text: str,
    links: list[tuple[str, Any]],
    *,
    initial_config: dict[str, Any],
) -> MediaPrepareAttempt | None:
    retry = _retry_settings(initial_config)
    attempts = retry["count"] + 1
    delay_seconds = retry["delay_seconds"]
    delay_403_base = retry["delay_403_base"]
    last_result: MediaPrepareAttempt | None = None

    for attempt in range(1, attempts + 1):
        is_403_retry = False
        try:
            result = await _prepare_links_once(bot, event, text, links)
        except Exception as e:
            if attempt >= attempts:
                raise
            is_403_retry = True
            logger.warning(
                "[MediaParser] parse/download attempt failed, retrying in %.1fs -> attempt=%d/%d error=%s",
                _pick_retry_delay(delay_seconds, delay_403_base, attempt, is_403_error=True),
                attempt,
                retry["count"],
                e,
                exc_info=True,
            )
        else:
            if result is None:
                return None
            if not _should_retry_prepare_result(result):
                if attempt > 1:
                    logger.info("[MediaParser] parse/download retry succeeded -> attempt=%d/%d", attempt, attempts)
                return result
            last_result = result
            if attempt >= attempts:
                return result
            is_403_retry = _has_403_failure(result) or _has_douyin_transient_error(result)
            logger.warning(
                "[MediaParser] parse/download produced retryable result, retrying in %.1fs -> attempt=%d/%d reason=%s",
                _pick_retry_delay(delay_seconds, delay_403_base, attempt, is_403_error=is_403_retry),
                attempt,
                retry["count"],
                _prepare_retry_reason(result),
            )

        current_delay = _pick_retry_delay(delay_seconds, delay_403_base, attempt, is_403_error=is_403_retry)
        if current_delay > 0:
            await asyncio.sleep(current_delay)

    return last_result


def _pick_retry_delay(
    normal_delay: float,
    delay_403_base: float,
    attempt: int,
    *,
    is_403_error: bool = False,
) -> float:
    """当 403 或服务端瞬时错误时使用指数递增延时，否则用固定延时。"""
    if not is_403_error:
        return normal_delay
    return delay_403_base * (2 ** (attempt - 1))


_DOUYIN_TRANSIENT_PATTERNS = (
    "-404",
    "啥都木有",
    "view error",
    "内容暂时无法",
)


def _has_douyin_transient_error(result: MediaPrepareAttempt) -> bool:
    """判断是否抖音服务端瞬时错误（触发更长的退避重试）。"""
    reason = _prepare_retry_reason(result)
    return any(pattern in reason for pattern in _DOUYIN_TRANSIENT_PATTERNS)


async def _prepare_links_once(
    bot: Bot,
    event: MessageEvent,
    text: str,
    links: list[tuple[str, Any]],
) -> MediaPrepareAttempt | None:
    runtime = _get_runtime()
    if runtime is None:
        return None
    links = [
        (url, parser) for url, parser in links
        if is_platform_allowed(getattr(parser, "name", "unknown"), event)
    ]
    if not links:
        return MediaPrepareAttempt(processed=[], metadata_list=[], config=runtime.config)
    links = _links_for_runtime(runtime, links)
    if not links:
        return MediaPrepareAttempt(processed=[], metadata_list=[], config=runtime.config)

    session = await _get_session()
    platform = getattr(links[0][1], "name", "unknown") if links else "unknown"
    label = f"解析 {platform}"
    with ActivityScope("media_parser", "parsing", label, description=links[0][0] if links else text):
        metadata_list = await runtime.parser_manager.parse_text(
            text,
            session,
            links_with_parser=links,
        )
        _trigger_bilibili_cookie_assist_if_needed(bot, runtime)
        if not metadata_list:
            return MediaPrepareAttempt(processed=[], metadata_list=[], config=runtime.config)
        raw_metadata_list = list(metadata_list)
        metadata_list = _suppress_redundant_error_metadata(metadata_list)

        processed: list[dict[str, Any]] = []
        max_send = max(1, int(runtime.config.get("max_send", 8)))
        cache_ttl_seconds = media_cache_ttl_seconds(runtime.config)
        for metadata in metadata_list:
            if not _apply_output_modes(runtime, metadata):
                continue
            if metadata.get("_enable_rich_media") and not metadata.get("error"):
                metadata = _limit_metadata_for_send(metadata, max_send=max_send)
                metadata = await runtime.download_manager.process_metadata(
                    session=session,
                    metadata=metadata,
                    proxy_addr=runtime.config_manager.proxy.address or None,
                )
                register_metadata_temp_media(metadata, ttl_seconds=cache_ttl_seconds)
            processed.append(metadata)

        return MediaPrepareAttempt(processed=processed, metadata_list=raw_metadata_list, config=runtime.config)


def _links_for_runtime(runtime: MediaParserRuntime, links: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
    refreshed: list[tuple[str, Any]] = []
    for url, parser in links:
        runtime_parser = runtime.parser_manager.find_parser(url)
        if runtime_parser is None:
            parser_name = getattr(parser, "name", "unknown")
            logger.debug("[MediaParser] parser disabled while retrying -> parser=%s url=%s", parser_name, url)
            continue
        refreshed.append((url, runtime_parser))
    return refreshed


def _should_retry_prepare_result(result: MediaPrepareAttempt) -> bool:
    if not result.processed:
        return not result.metadata_list or any(metadata.get("error") for metadata in result.metadata_list)

    for metadata in result.processed:
        if _metadata_has_sendable_media(metadata):
            return False
        if metadata.get("error"):
            return True
        if metadata.get("_enable_rich_media") and _metadata_has_retryable_download_failure(metadata):
            return True
    return False


def _metadata_has_sendable_media(metadata: dict[str, Any]) -> bool:
    if metadata.get("has_valid_media"):
        return True
    modes = list(metadata.get("video_modes") or []) + list(metadata.get("image_modes") or [])
    return any(mode in ("local", "direct") for mode in modes)


def _has_403_failure(result: MediaPrepareAttempt) -> bool:
    """Check if any metadata in the result has a 403 Forbidden failure."""
    for metadata in result.processed:
        if metadata.get("error") and "403" in str(metadata.get("error")):
            return True
        if not metadata.get("_enable_rich_media"):
            continue
        status_codes = list(metadata.get("video_status_codes") or [])
        status_codes.extend(metadata.get("image_status_codes") or [])
        for code in status_codes:
            if _is_403_code(code):
                return True
        for reason in _metadata_skip_reasons(metadata):
            if "403" in reason or "Forbidden" in reason:
                return True
    return False


def _is_403_code(code: Any) -> bool:
    try:
        return int(code) == 403
    except (TypeError, ValueError):
        return False


def _metadata_has_retryable_download_failure(metadata: dict[str, Any]) -> bool:
    media_count = int(metadata.get("video_count", len(metadata.get("video_urls") or [])))
    media_count += int(metadata.get("image_count", len(metadata.get("image_urls") or [])))
    if media_count <= 0:
        return False

    status_codes = list(metadata.get("video_status_codes") or []) + list(metadata.get("image_status_codes") or [])
    for code in status_codes:
        if _is_retryable_status_code(code):
            return True

    terminal_tokens = ("超过限制", "缓存目录不可用", "权限")
    retry_tokens = ("缓存下载失败", "下载媒体失败", "HTTP 404", "403", "Forbidden", "timeout", "timed out", "超时")
    for reason in _metadata_skip_reasons(metadata):
        if any(token in reason for token in terminal_tokens):
            continue
        if any(token in reason for token in retry_tokens):
            return True
    return False


def _is_retryable_status_code(code: Any) -> bool:
    try:
        status_code = int(code)
    except (TypeError, ValueError):
        return False
    return status_code in {403, 404, 408, 409, 425, 429} or status_code >= 500


def _metadata_skip_reasons(metadata: dict[str, Any]) -> list[str]:
    reasons = []
    for value in (metadata.get("video_skip_reasons") or []) + (metadata.get("image_skip_reasons") or []):
        if value:
            reasons.append(str(value))
    return reasons


def _prepare_retry_reason(result: MediaPrepareAttempt) -> str:
    if not result.metadata_list:
        return "empty metadata"
    errors = [str(metadata.get("error")) for metadata in result.metadata_list if metadata.get("error")]
    if errors:
        return errors[0][:160]
    for metadata in result.processed:
        reasons = _metadata_skip_reasons(metadata)
        if reasons:
            return reasons[0][:160]
    return "no sendable media"


def _limit_metadata_for_send(metadata: dict[str, Any], *, max_send: int) -> dict[str, Any]:
    video_only = bool(metadata.get("_video_only"))
    video_urls = list(metadata.get("video_urls") or [])
    original_images = list(metadata.get("image_urls") or [])
    # 「仅视频」平台在下载前就把图片丢掉：既不下载也不发送，省掉大量无用请求。
    image_urls = [] if video_only else original_images
    total_count = len(video_urls) + len(image_urls)
    original_total = len(video_urls) + len(original_images)
    if total_count <= max_send and len(image_urls) == len(original_images):
        return metadata

    keep_video_count = min(len(video_urls), max_send)
    keep_image_count = 0 if video_only else max(0, max_send - keep_video_count)
    limited = dict(metadata)
    limited["_original_video_count"] = len(video_urls)
    limited["_original_image_count"] = len(original_images)
    limited["video_urls"] = video_urls[:keep_video_count]
    limited["image_urls"] = image_urls[:keep_image_count]
    _slice_metadata_list(limited, "video_cover_urls", keep_video_count)
    _slice_metadata_list(limited, "video_cover_url_lists", keep_video_count)
    _slice_metadata_list(limited, "video_force_downloads", keep_video_count)
    logger.info(
        "[MediaParser] media list limited before download -> platform=%s original=%d keep=%d%s",
        metadata.get("platform") or metadata.get("parser_name") or "unknown",
        original_total,
        keep_video_count + keep_image_count,
        " (video-only)" if video_only else "",
    )
    return limited


def _slice_metadata_list(metadata: dict[str, Any], key: str, limit: int) -> None:
    value = metadata.get(key)
    if isinstance(value, list):
        metadata[key] = value[:limit]


def _suppress_redundant_error_metadata(metadata_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop failed candidate links when the same message produced a success."""
    successes = [item for item in metadata_list if not item.get("error")]
    failures = [item for item in metadata_list if item.get("error")]
    if not successes or not failures:
        return metadata_list

    for item in failures:
        logger.info(
            "[MediaParser] suppress failed candidate because another candidate succeeded -> "
            "platform=%s url=%s error=%s",
            item.get("platform") or item.get("parser_name") or "unknown",
            item.get("source_url") or item.get("url") or "",
            item.get("error"),
        )
    return successes
