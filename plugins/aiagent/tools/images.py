"""AI Agent 的网络搜图与发图工具（`web_image_search` 与 `send_images`）。

数据来源：
- 图片搜索只走 SearXNG 的 `categories=images`（format=json）。
- DeepSeek 官方 Search API 没有图片搜索能力，因此图片搜索不依赖 DeepSeek Key。

安全防护：
- 搜图结果写入按会话隔离的内存缓存（TTL 30 分钟，最多 50 条）。
- `send_images` 只能发送命中该会话搜图缓存的 URL，或该会话工作区内的图片路径，
  防止模型拿任意 URL 触发内网下载或 SSRF。
- 图片下载做 SSRF 防护：拒绝 localhost / 私有 IP / 回环 / 保留地址，逐跳校验重定向；
  但允许配置里的 SearXNG 主机名通过图片代理。
- WebP 或超大图片转码为 PNG/JPEG，GIF 保留动画。
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import ipaddress
import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from ..utils import safe_bool, safe_int

logger = logging.getLogger("HikariBot.AIAgent.Tools.Images")

WEB_IMAGE_SEARCH = "web_image_search"
SEND_IMAGES = "send_images"

_CACHE_TTL_SECONDS = 1800  # 30 分钟
_MAX_CACHE_ITEMS_PER_SESSION = 50
_TEMP_IMAGE_DIR = Path("/tmp/hikari_bot/aiagent/images")

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 3

# 会话缓存：session_id -> list of {"image_url": str, "title": str, "cached_at": float}
_session_image_cache: dict[str, list[dict[str, Any]]] = {}


def _tools_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    return cfg.get("tools") if isinstance(cfg.get("tools"), dict) else {}


def config(cfg: dict[str, Any]) -> dict[str, Any]:
    tools_cfg = _tools_cfg(cfg)
    return tools_cfg.get("images") if isinstance(tools_cfg.get("images"), dict) else {}


def enabled(cfg: dict[str, Any]) -> bool:
    return safe_bool(config(cfg).get("enabled"), True)


def allow_send(cfg: dict[str, Any]) -> bool:
    return safe_bool(config(cfg).get("allow_send"), False)


def can_handle(name: str) -> bool:
    return name in {WEB_IMAGE_SEARCH, SEND_IMAGES}


# ── 会话缓存管理 ──────────────────────────────────────────────────────────


def get_session_id(context: Any) -> str:
    event = getattr(context, "event", None)
    if event is None:
        return "default"
    try:
        from ..memory import session_key
        return session_key(event)
    except Exception:
        user_id = getattr(event, "user_id", None) or "default"
        return f"private:{user_id}"


def record_session_images(session_id: str, results: list[dict[str, Any]]) -> None:
    now = time.time()
    existing = _session_image_cache.get(session_id, [])
    valid = [item for item in existing if now - item.get("cached_at", 0) < _CACHE_TTL_SECONDS]
    for r in results:
        url = str(r.get("image_url") or "").strip()
        if url and not any(item.get("image_url") == url for item in valid):
            valid.append({"image_url": url, "title": str(r.get("title") or ""), "cached_at": now})
    if len(valid) > _MAX_CACHE_ITEMS_PER_SESSION:
        valid = valid[-_MAX_CACHE_ITEMS_PER_SESSION:]
    _session_image_cache[session_id] = valid


def is_url_in_session_cache(session_id: str, url: str) -> bool:
    now = time.time()
    existing = _session_image_cache.get(session_id, [])
    target = url.strip()
    return any(item.get("image_url") == target and now - item.get("cached_at", 0) < _CACHE_TTL_SECONDS for item in existing)


def clear_session_cache(session_id: str | None = None) -> None:
    if session_id is None:
        _session_image_cache.clear()
    else:
        _session_image_cache.pop(session_id, None)


# ── 工具定义 ──────────────────────────────────────────────────────────────


def _search_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": WEB_IMAGE_SEARCH,
            "description": (
                "Search for images on the web using SearXNG. Returns image titles, page URLs, "
                "direct image URLs, thumbnail URLs, resolutions, and formats. "
                "Results are cached for the current session. "
                "To send one or more images to the chat, call send_images with the image_url."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Image search query.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of image results to return (1-10).",
                        "minimum": 1,
                        "maximum": 10,
                    },
                    "safesearch": {
                        "type": "integer",
                        "description": "0=None, 1=Moderate, 2=Strict.",
                        "minimum": 0,
                        "maximum": 2,
                    },
                    "time_range": {
                        "type": "string",
                        "description": "Optional time filter: day, week, month, or year.",
                        "enum": ["day", "week", "month", "year"],
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }


def _send_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": SEND_IMAGES,
            "description": (
                "Send images to the current chat session. "
                "Accepts image URLs previously found via web_image_search in this session, "
                "or relative paths to image files inside the session workspace. "
                "At most 3 images can be sent per turn."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of direct image URLs from web_image_search results.",
                    },
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of relative paths to images in the session workspace.",
                    },
                    "max_images": {
                        "type": "integer",
                        "description": "Maximum number of images to send (at most 3).",
                        "minimum": 1,
                        "maximum": 3,
                    },
                },
                "additionalProperties": False,
            },
        },
    }


def definitions(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    defs = [_search_definition()]
    if cfg is not None and allow_send(cfg):
        defs.append(_send_definition())
    return defs


# ── SSRF 与安全下载 ────────────────────────────────────────────────────────


def _searxng_host(cfg: dict[str, Any]) -> str:
    tools_cfg = _tools_cfg(cfg)
    search_cfg = tools_cfg.get("search") if isinstance(tools_cfg.get("search"), dict) else {}
    base_url = str(search_cfg.get("base_url") or "http://searxng-core:8080").strip()
    try:
        return (urlparse(base_url).hostname or "").strip().lower()
    except Exception:
        return ""


def _literal_ip(host: str) -> Any | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _ip_is_public(raw_ip: str) -> bool:
    address = _literal_ip(raw_ip)
    if address is None:
        return False
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


async def _resolve_host_ips(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None)
    return [str(info[4][0]) for info in infos if info[4]]


async def _host_is_allowed(host: str, allowed_host: str = "") -> bool:
    h = host.strip().lower().rstrip(".")
    if not h or h in {"localhost", "localhost.localdomain"}:
        return False
    if allowed_host and h == allowed_host.lower().rstrip("."):
        return True
    if _literal_ip(h) is not None:
        return _ip_is_public(h)
    try:
        addresses = await _resolve_host_ips(h)
    except Exception as e:
        logger.warning("[AIAgent] 图片域名解析失败 %.80s: %s", h, e)
        return False
    if not addresses:
        return False
    return all(_ip_is_public(address) for address in addresses)


def _is_safe_url(raw_url: str, allowed_host: str = "") -> bool:
    try:
        parsed = urlparse(raw_url)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            return False
        if parsed.port not in {None, 80, 443, 8080}:
            return False
        host = (parsed.hostname or "").strip().lower().rstrip(".")
        if not host:
            return False
        if allowed_host and host == allowed_host.lower().rstrip("."):
            return True
        if host in {"localhost", "localhost.localdomain"}:
            return False
        literal = _literal_ip(host)
        if literal is None:
            return True
        return _ip_is_public(host)
    except Exception:
        return False


async def _download_single_image(
    client: httpx.AsyncClient,
    url: str,
    max_bytes: int,
    allowed_host: str = "",
) -> tuple[bytes, str] | None:
    current_url = url
    for _ in range(_MAX_REDIRECTS + 1):
        if not _is_safe_url(current_url, allowed_host):
            logger.warning("[AIAgent] 图片 URL 未通过结构校验: %.80s", current_url)
            return None
        parsed = urlparse(current_url)
        host = (parsed.hostname or "").strip().lower()
        if not await _host_is_allowed(host, allowed_host):
            logger.warning("[AIAgent] 图片主机未通过 SSRF 检查: %.80s", host)
            return None

        try:
            async with client.stream("GET", current_url) as resp:
                status = int(resp.status_code)
                if status in _REDIRECT_STATUSES:
                    loc = resp.headers.get("location") or ""
                    if not loc:
                        return None
                    current_url = urljoin(current_url, loc)
                    continue
                if status >= 400:
                    logger.warning("[AIAgent] 图片下载 HTTP %d: %.80s", status, current_url)
                    return None
                buffer = bytearray()
                async for chunk in resp.aiter_bytes():
                    buffer.extend(chunk)
                    if len(buffer) > max_bytes:
                        logger.warning("[AIAgent] 图片超过 %d 字节上限: %.80s", max_bytes, current_url)
                        return None
                data = bytes(buffer)
                fmt = _detect_format(data)
                return data, fmt
        except Exception as e:
            logger.warning("[AIAgent] 图片下载失败 %.80s: %s", current_url, e)
            return None
    return None


def _detect_format(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return "bin"


def _process_and_save_image(raw_bytes: bytes, fmt: str) -> Path:
    _TEMP_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.md5(raw_bytes).hexdigest()

    if fmt == "gif":
        out_path = _TEMP_IMAGE_DIR / f"{digest}.gif"
        out_path.write_bytes(raw_bytes)
        return out_path

    from PIL import Image

    bio = io.BytesIO(raw_bytes)
    with Image.open(bio) as img:
        if getattr(img, "is_animated", False):
            out_path = _TEMP_IMAGE_DIR / f"{digest}.gif"
            img.save(out_path, format="GIF", save_all=True)
            return out_path
        if fmt == "webp" or img.mode not in ("RGB", "RGBA"):
            # 转为 PNG
            out_path = _TEMP_IMAGE_DIR / f"{digest}.png"
            img.convert("RGBA").save(out_path, format="PNG")
            return out_path
        if fmt == "jpeg":
            out_path = _TEMP_IMAGE_DIR / f"{digest}.jpg"
            out_path.write_bytes(raw_bytes)
            return out_path
        out_path = _TEMP_IMAGE_DIR / f"{digest}.png"
        out_path.write_bytes(raw_bytes)
        return out_path


# ── 执行工具 ──────────────────────────────────────────────────────────────


async def execute(
    name: str,
    cfg: dict[str, Any],
    arguments: dict[str, Any],
    context: Any = None,
) -> str:
    if name == WEB_IMAGE_SEARCH:
        return await _execute_search(cfg, arguments, context)
    if name == SEND_IMAGES:
        if not allow_send(cfg):
            return json.dumps({"error": "send_images is disabled by configuration"}, ensure_ascii=False)
        return await _execute_send(cfg, arguments, context)
    return json.dumps({"error": f"unknown image tool: {name}"}, ensure_ascii=False)


async def _execute_search(
    cfg: dict[str, Any],
    arguments: dict[str, Any],
    context: Any = None,
) -> str:
    img_cfg = config(cfg)
    tools_cfg = _tools_cfg(cfg)
    search_cfg = tools_cfg.get("search") if isinstance(tools_cfg.get("search"), dict) else {}

    query = str(arguments.get("query") or "").strip()
    if not query:
        return json.dumps({"query": "", "error": "query is required"}, ensure_ascii=False)

    configured_max = safe_int(img_cfg.get("max_results"), 5, minimum=1, maximum=10)
    max_results = safe_int(arguments.get("max_results"), configured_max, minimum=1, maximum=10)
    safesearch = safe_int(arguments.get("safesearch"), safe_int(img_cfg.get("safesearch"), 1, minimum=0, maximum=2), minimum=0, maximum=2)
    time_range = str(arguments.get("time_range") or "").strip()

    base_url = str(search_cfg.get("base_url") or "http://searxng-core:8080").strip().rstrip("/")
    if not base_url:
        base_url = "http://searxng-core:8080"
    url = f"{base_url}/search" if not base_url.endswith("/search") else base_url

    params: dict[str, Any] = {
        "q": query,
        "format": "json",
        "categories": "images",
        "safesearch": safesearch,
    }
    if time_range:
        params["time_range"] = time_range

    timeout_seconds = safe_int(img_cfg.get("download_timeout_seconds"), 20, minimum=1, maximum=120)
    timeout = httpx.Timeout(timeout_seconds)
    proxy = str(search_cfg.get("proxy") or "").strip() or None

    try:
        async with httpx.AsyncClient(timeout=timeout, proxy=proxy) as client:
            response = await client.get(url, params=params)
    except Exception as e:
        logger.warning("[AIAgent] SearXNG 图片搜索请求失败: %s", e)
        return json.dumps({"query": query, "error": f"image search request failed: {e}"}, ensure_ascii=False)

    if response.status_code >= 400:
        logger.warning("[AIAgent] SearXNG 图片搜索 HTTP %d", response.status_code)
        return json.dumps(
            {"query": query, "error": f"SearXNG image search HTTP {response.status_code}", "detail": response.text[:300]},
            ensure_ascii=False,
        )

    try:
        data = response.json()
    except Exception:
        return json.dumps({"query": query, "error": "SearXNG returned invalid JSON"}, ensure_ascii=False)

    raw_results = data.get("results") if isinstance(data, dict) else []
    results: list[dict[str, Any]] = []

    if isinstance(raw_results, list):
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            image_url = str(item.get("img_src") or "").strip()
            if not image_url:
                continue
            if image_url.startswith("/"):
                image_url = urljoin(base_url, image_url)

            thumb_url = str(item.get("thumbnail_src") or item.get("thumbnail") or "").strip()
            if thumb_url and thumb_url.startswith("/"):
                thumb_url = urljoin(base_url, thumb_url)

            results.append(
                {
                    "title": str(item.get("title") or "").strip()[:200],
                    "page_url": str(item.get("url") or "").strip()[:500],
                    "image_url": image_url,
                    "thumbnail_url": thumb_url,
                    "resolution": str(item.get("resolution") or "").strip()[:50],
                    "format": str(item.get("format") or "").strip()[:20],
                    "engine": str(item.get("engine") or "").strip()[:80],
                }
            )
            if len(results) >= max_results:
                break

    sid = get_session_id(context)
    record_session_images(sid, results)

    payload = {
        "query": query,
        "results": results,
        "notice": "图片链接只对本次会话有效；发图请调用 send_images",
    }
    return json.dumps(payload, ensure_ascii=False)


async def _execute_send(
    cfg: dict[str, Any],
    arguments: dict[str, Any],
    context: Any = None,
) -> str:
    img_cfg = config(cfg)
    sid = get_session_id(context)

    raw_urls = arguments.get("urls")
    raw_paths = arguments.get("paths")

    urls = [str(u).strip() for u in raw_urls if str(u).strip()] if isinstance(raw_urls, list) else []
    paths = [str(p).strip() for p in raw_paths if str(p).strip()] if isinstance(raw_paths, list) else []

    if not urls and not paths:
        return json.dumps({"ok": False, "error": "urls or paths is required"}, ensure_ascii=False)

    configured_limit = safe_int(img_cfg.get("max_per_send"), 3, minimum=1, maximum=3)
    requested_limit = safe_int(arguments.get("max_images"), configured_limit, minimum=1, maximum=3)
    max_images = min(configured_limit, requested_limit)

    saved_image_paths: list[Path] = []
    failed: list[dict[str, str]] = []

    # 1. 验证工作区路径图片
    if paths:
        try:
            from .workspace import get_session_workspace_root, resolve_session_path
            workspace_root = get_session_workspace_root(context)
        except ImportError:
            workspace_root = None

        for p_str in paths:
            if len(saved_image_paths) >= max_images:
                break
            if workspace_root is None:
                failed.append({"target": p_str, "error": "workspace is not available"})
                continue
            try:
                resolved = resolve_session_path(workspace_root, p_str)
                if not resolved.is_file():
                    failed.append({"target": p_str, "error": "file not found in workspace"})
                    continue
                fmt = _detect_format(resolved.read_bytes()[:32])
                if fmt not in {"jpeg", "png", "gif", "webp"}:
                    failed.append({"target": p_str, "error": "file is not a supported image format"})
                    continue
                saved_image_paths.append(resolved)
            except Exception as e:
                failed.append({"target": p_str, "error": str(e)})

    # 2. 校验并下载网络图片
    if urls and len(saved_image_paths) < max_images:
        max_bytes = safe_int(img_cfg.get("max_bytes"), 10_485_760, minimum=65536, maximum=33_554_432)
        timeout_seconds = safe_int(img_cfg.get("download_timeout_seconds"), 20, minimum=3, maximum=120)
        allowed_searxng = _searxng_host(cfg)

        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds), follow_redirects=False) as client:
            for url in urls:
                if len(saved_image_paths) >= max_images:
                    break
                if not is_url_in_session_cache(sid, url):
                    failed.append({"target": url, "error": "URL not found in session image search cache"})
                    continue

                res = await _download_single_image(client, url, max_bytes, allowed_host=allowed_searxng)
                if res is None:
                    failed.append({"target": url, "error": "download failed or blocked by security check"})
                    continue

                raw_bytes, fmt = res
                try:
                    out_path = await asyncio.to_thread(_process_and_save_image, raw_bytes, fmt)
                    saved_image_paths.append(out_path)
                except Exception as e:
                    logger.warning("[AIAgent] 图片转码/保存失败: %s", e)
                    failed.append({"target": url, "error": f"image processing failed: {e}"})

    if not saved_image_paths:
        return json.dumps(
            {"ok": False, "sent": 0, "failed": failed, "error": "no images were successfully prepared"},
            ensure_ascii=False,
        )

    bot = getattr(context, "bot", None)
    event = getattr(context, "event", None)
    if bot is None or event is None:
        return json.dumps(
            {
                "ok": True,
                "sent": len(saved_image_paths),
                "failed": failed,
                "paths": [str(p) for p in saved_image_paths],
                "notice": "images prepared but no bot context to send message",
            },
            ensure_ascii=False,
        )

    try:
        segments = [MessageSegment.image(p.resolve().as_uri()) for p in saved_image_paths]
        await bot.send(event, Message(segments))
    except Exception as e:
        logger.warning("[AIAgent] 发送图片消息失败: %s", e)
        return json.dumps(
            {
                "ok": False,
                "sent": 0,
                "failed": failed,
                "error": f"failed to send images to chat: {e}",
            },
            ensure_ascii=False,
        )

    return json.dumps(
        {
            "ok": True,
            "sent": len(saved_image_paths),
            "failed": failed,
            "paths": [str(p) for p in saved_image_paths],
        },
        ensure_ascii=False,
    )
