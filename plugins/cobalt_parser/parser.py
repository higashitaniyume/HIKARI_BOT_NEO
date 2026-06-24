"""
Cobalt 媒体解析模块。

负责：
1. Instagram / Facebook URL 正则匹配
2. 调用 cobalt API 获取下载链接
3. 数据结构定义
"""

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

logger = logging.getLogger("HikariBot.CobaltParser")

# =========================
# URL 正则
# =========================

# Instagram: /p/CODE, /reel/CODE, /stories/USER/ID, /tv/CODE
INSTAGRAM_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?instagram\.com"
    r"/(?:p|reel|stories|tv)/[\w\-]+"
    r"(?:/[^?\s]*)?(?:\?[^\s]*)?",
    re.IGNORECASE,
)

# Facebook: facebook.com/..., fb.com/..., fb.watch/...
FACEBOOK_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?"
    r"(?:facebook\.com/(?:[^?\s]+)"
    r"|fb\.com/(?:[^?\s]+)"
    r"|fb\.watch/(?:[^?\s]+))",
    re.IGNORECASE,
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.7103.48 Safari/537.36"
)
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

# =========================
# 数据结构
# =========================


@dataclass
class CobaltMediaItem:
    """cobalt 返回的单个媒体项。"""
    index: int
    url: str
    media_type: str  # "photo" / "video" / "gif"
    thumb_url: str = ""


@dataclass
class CobaltResult:
    """cobalt 解析结果。"""
    source_url: str
    status: str  # "single" / "picker" / "error"
    service: str = ""  # instagram / facebook / etc
    items: list[CobaltMediaItem] = field(default_factory=list)
    audio_url: str = ""
    audio_filename: str = ""
    error_code: str = ""
    error_context: dict[str, Any] = field(default_factory=dict)


# =========================
# URL 提取
# =========================


def extract_social_urls(text: str) -> list[str]:
    """从文本中提取所有 Instagram / Facebook URL（去重，保持顺序）。"""
    urls: list[str] = []
    seen: set[str] = set()

    for match in INSTAGRAM_URL_RE.finditer(text):
        url = match.group(0)
        if url not in seen:
            seen.add(url)
            urls.append(url)

    for match in FACEBOOK_URL_RE.finditer(text):
        url = match.group(0)
        if url not in seen:
            seen.add(url)
            urls.append(url)

    return urls


# =========================
# Cobalt API
# =========================


async def call_cobalt_api(
    source_url: str,
    api_endpoint: str,
    api_key: str = "",
    timeout: int = 90,
) -> CobaltResult:
    """
    调用 cobalt API 解析一个社交媒体 URL。

    Args:
        source_url: 要解析的社交媒体 URL
        api_endpoint: cobalt API 地址，如 http://192.168.31.2:54257/
        api_key: API Key（可选）
        timeout: 请求超时（秒）

    Returns:
        CobaltResult 对象
    """
    if not api_endpoint.endswith("/"):
        api_endpoint += "/"
    api_origin = _origin_for_url(api_endpoint)

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }

    if api_key:
        headers["Authorization"] = f"Api-Key {api_key}"

    body: dict[str, Any] = {
        "url": source_url,
        "filenameStyle": "basic",
        "downloadMode": "auto",
    }

    logger.info(f"[Cobalt] 请求 API → {source_url[:80]}...")
    t_start = time.time()

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=20.0)) as client:
        resp = await client.post(api_endpoint, json=body, headers=headers)

        try:
            data = resp.json()
        except Exception as e:
            resp.raise_for_status()
            raise RuntimeError(f"Cobalt API JSON 解析失败：{e} body={resp.text[:300]!r}") from e

        if resp.is_error and data.get("status") != "error":
            resp.raise_for_status()

    elapsed = time.time() - t_start
    status = data.get("status", "error")

    if status == "error":
        error_obj = data.get("error", {})
        error_code = error_obj.get("code", "unknown")
        logger.warning(
            f"[Cobalt] API 返回错误 → {source_url[:60]}... code={error_code} ({elapsed:.2f}s)"
        )
        return CobaltResult(
            source_url=source_url,
            status="error",
            error_code=error_code,
            error_context=error_obj.get("context", {}),
        )

    if status == "picker":
        picker_items = data.get("picker", [])
        audio_url = data.get("audio", "")
        audio_filename = data.get("audioFilename", "")
        items: list[CobaltMediaItem] = []

        for i, item in enumerate(picker_items):
            item_url = _normalize_cobalt_media_url(item.get("url", ""), api_origin)
            items.append(CobaltMediaItem(
                index=i,
                url=item_url,
                media_type=item.get("type", "photo"),
                thumb_url=_normalize_cobalt_media_url(item.get("thumb", ""), api_origin),
            ))

        logger.info(
            f"[Cobalt] picker 响应 → {len(items)} 个媒体项"
            + (f", 含背景音频" if audio_url else "")
            + f" ({elapsed:.2f}s)"
        )
        return CobaltResult(
            source_url=source_url,
            status="picker",
            items=items,
            audio_url=audio_url,
            audio_filename=audio_filename,
        )

    # tunnel / redirect / local-processing: 单个媒体
    download_url = _normalize_cobalt_media_url(data.get("url", ""), api_origin)
    filename = data.get("filename", "media")
    service = data.get("service", "")

    # 根据服务名和文件名推断 media_type
    media_type = _guess_media_type(filename, download_url, service)

    logger.info(
        f"[Cobalt] {status} 响应 → {filename} ({media_type})"
        + (f", service={service}" if service else "")
        + f" ({elapsed:.2f}s)"
    )
    return CobaltResult(
        source_url=source_url,
        status="single",
        service=service,
        items=[CobaltMediaItem(index=0, url=download_url, media_type=media_type)],
    )


def _guess_media_type(filename: str, url: str, service: str) -> str:
    """根据文件名、URL 和服务推断媒体类型。"""
    filename_lower = filename.lower()
    url_lower = url.lower()

    if any(ext in filename_lower or ext in url_lower for ext in [".mp4", ".webm", ".mov", ".mkv"]):
        return "video"
    if any(ext in filename_lower or ext in url_lower for ext in [".gif"]):
        return "gif"
    if any(ext in filename_lower or ext in url_lower for ext in [".jpg", ".jpeg", ".png", ".webp"]):
        return "photo"

    # 根据服务推断：instagram 多为 photo，facebook 多为 video
    if service == "instagram":
        return "photo"
    return "video"


def _origin_for_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    return parsed.scheme, parsed.netloc


def _normalize_cobalt_media_url(url: str, api_origin: tuple[str, str]) -> str:
    """把 cobalt 返回的 loopback tunnel URL 改成机器人可访问的 API origin。"""
    if not url:
        return ""

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in LOOPBACK_HOSTS:
        return url

    api_scheme, api_netloc = api_origin
    if not api_scheme or not api_netloc:
        return url

    normalized = parsed._replace(scheme=api_scheme, netloc=api_netloc)
    return urlunparse(normalized)
