"""给 AI Agent 加「眼睛」：把 QQ 消息里的图片转成模型能吃的图片块。

DeepSeek 的 deepseek-flash 原生支持图片输入
（https://api-docs.deepseek.com/zh-cn/guides/vision；deepseek-v4-pro 不支持）：

- Chat Completions：content 变成块数组，图片块是
  ``{"type": "image_url", "image_url": {"url": ..., "detail": ...}}``；
- Responses API：图片块是 ``{"type": "input_image", "image_url": <字符串>,
  "detail": ...}``，文本块要写成 ``input_text``；
- 只有 user 消息能带图片，system / assistant 带图片会 400；
- detail=low 会把图片缩到 512×512；high / original / auto 保留原图，
  每张图最多按 1024 token 计费。

QQ 图床直链带鉴权参数且时效很短，模型侧未必拉得到，所以这里先本地下载再转成
base64 data URI 发出去。格式按字节头识别（文档明确说不看文件名和 MIME 声明）；
尺寸或体积过大的图先用 Pillow 压到模型友好的大小，避免超过 API 的单边 8192px
与体积限制，也避免请求体过大。

下载侧做了 SSRF 防护（见 `_host_is_allowed` / `_download_image`）：
只允许 http/https 默认端口，拒绝 localhost、私有/回环/链路本地/保留地址，
域名按 DNS 解析结果逐个校验，重定向自己跟随并逐跳重新校验目标。
"""

from __future__ import annotations

import asyncio
import base64
import io
import ipaddress
import logging
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from .utils import safe_int

logger = logging.getLogger("HikariBot.AIAgent.Vision")

VISION_DETAILS = ("low", "high", "original", "auto")

# 官方 DeepSeek 端点与目前唯一支持图像理解的模型。
_DEEPSEEK_HOST = "api.deepseek.com"
_VISION_MODEL = "deepseek-flash"

# 模型侧会把图片缩到总像素约 1300×1300，再大只是白占上传带宽。
_MAX_EDGE = 2048
# 超过这个体积就先重新编码，避免请求体被几张高码率截图撑大。
_PASSTHROUGH_BYTES = 3 * 1024 * 1024

# 重定向自己跟随，以便逐跳重新校验目标主机（避免 302 到内网的 SSRF）。
_MAX_REDIRECTS = 3
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}

_MIME_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def vision_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    section = cfg.get("vision") if isinstance(cfg.get("vision"), dict) else {}
    detail = str(section.get("detail") or "auto").strip().lower()
    return {
        "enabled": bool(section.get("enabled", True)),
        "model": str(section.get("model") or "").strip(),
        "max_images": safe_int(section.get("max_images"), 2, minimum=1, maximum=8),
        "detail": detail if detail in VISION_DETAILS else "auto",
        "include_quoted": bool(section.get("include_quoted", True)),
        "max_bytes": safe_int(section.get("max_bytes"), 10_485_760, minimum=65536, maximum=33_554_432),
        "download_timeout_seconds": safe_int(
            section.get("download_timeout_seconds"), 20, minimum=3, maximum=120
        ),
    }


def request_model(cfg: dict[str, Any], has_image: bool) -> str:
    """当前请求该用哪个模型。

    不带图时永远是主模型；带图时优先 `vision.model`，其次在官方
    api.deepseek.com 端点下自动切到 deepseek-flash（目前只有它支持图像理解，
    deepseek-v4-pro 会直接 400）。其他厂商端点不做猜测，沿用主模型。
    """
    model_cfg = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
    main_model = str(model_cfg.get("model") or "").strip()
    if not has_image:
        return main_model

    override = vision_cfg(cfg)["model"]
    if override:
        return override

    try:
        host = (urlparse(str(model_cfg.get("base_url") or "")).hostname or "").strip().lower()
    except ValueError:
        host = ""
    if host == _DEEPSEEK_HOST and main_model.lower() != _VISION_MODEL:
        logger.debug("[AIAgent] 主模型 %s 不支持图片输入，带图请求改用 %s", main_model, _VISION_MODEL)
        return _VISION_MODEL
    return main_model


def collect_image_urls(event: Any, cfg: dict[str, Any]) -> list[str]:
    """按顺序取当前消息（可选：被引用消息）里的图片直链，去重后截断到上限。"""
    settings = vision_cfg(cfg)
    if not settings["enabled"]:
        return []

    urls: list[str] = []
    seen: set[str] = set()

    def absorb(message: Any) -> None:
        for segment in message or []:
            if str(getattr(segment, "type", "") or "") != "image":
                continue
            data = getattr(segment, "data", None)
            if not isinstance(data, dict):
                continue
            url = _pick_url(data)
            if url and url not in seen:
                seen.add(url)
                urls.append(url)

    if settings["include_quoted"]:
        reply = getattr(event, "reply", None)
        absorb(getattr(reply, "message", None) if reply is not None else None)
    try:
        absorb(event.get_message())
    except Exception:  # 非消息事件
        pass
    return urls[: settings["max_images"]]


async def build_image_blocks(urls: list[str], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """下载图片并转成 Chat Completions 形状的图片块；拉不到的直接丢掉。"""
    if not urls:
        return []
    settings = vision_cfg(cfg)
    timeout = httpx.Timeout(settings["download_timeout_seconds"])
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        downloads = await asyncio.gather(
            *(_download_image(client, url, settings["max_bytes"]) for url in urls)
        )

    blocks: list[dict[str, Any]] = []
    for item in downloads:
        if not item:
            continue
        raw, mime = item
        # 解码/缩放是同步 CPU 活，丢到线程里，别卡住事件循环。
        raw, mime = await asyncio.to_thread(_prepare_image, raw, mime)
        data_uri = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
        blocks.append({"type": "image_url", "image_url": {"url": data_uri, "detail": settings["detail"]}})
    return blocks


def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def content_text(content: Any) -> str:
    """从 str 或块数组里取出纯文本（块数组兼容 Chat / Responses 两种文本块名）。"""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [
        str(block.get("text") or "")
        for block in content
        if isinstance(block, dict) and block.get("type") in {"text", "input_text"}
    ]
    return "\n".join(part for part in parts if part)


def has_images(messages: list[dict[str, Any]]) -> bool:
    return any(isinstance(message.get("content"), list) for message in messages)


def strip_images(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把块数组内容压回纯文本，用于模型不支持图片时的降级重试。"""
    stripped: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message.get("content"), list):
            message = {**message, "content": content_text(message["content"])}
        stripped.append(message)
    return stripped


def _pick_url(data: dict[str, Any]) -> str:
    for key in ("url", "file"):
        value = str(data.get(key) or "").strip()
        if value.startswith(("http://", "https://")) and _is_safe_url(value):
            return value
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


def _is_safe_url(raw_url: str) -> bool:
    """结构层面的 URL 检查（不含 DNS 解析）。

    只允许 http/https、默认端口、无 userinfo，并拒绝 localhost 与字面量私有 IP。
    域名是否指向内网由 `_host_is_allowed` 在下载前用 DNS 解析结果判断。
    """
    try:
        parsed = urlparse(raw_url)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            return False
        if parsed.port not in {None, 80, 443}:
            return False
        host = (parsed.hostname or "").strip().lower().rstrip(".")
        if not host or host in {"localhost", "localhost.localdomain"}:
            return False
        literal = _literal_ip(host)
        if literal is None:
            return True
        return _ip_is_public(host)
    except ValueError:
        return False


async def _resolve_host_ips(host: str) -> list[str]:
    """解析域名得到 IP 列表（独立函数便于测试替换）。"""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None)
    return [str(info[4][0]) for info in infos if info[4]]


async def _host_is_allowed(host: str) -> bool:
    """下载前的最终主机校验：localhost / 私有 IP / 解析到内网的域名一律拒绝。"""
    if not host or host in {"localhost", "localhost.localdomain"}:
        return False
    if _literal_ip(host) is not None:
        return _ip_is_public(host)
    try:
        addresses = await _resolve_host_ips(host)
    except Exception as e:
        logger.warning("[AIAgent] 图片域名解析失败 %.80s: %s", host, e)
        return False
    if not addresses:
        return False
    return all(_ip_is_public(address) for address in addresses)


def _header(response: Any, name: str) -> str:
    headers = getattr(response, "headers", None)
    if headers is None:
        return ""
    try:
        return str(headers.get(name) or "").strip()
    except Exception:
        return ""


async def _fetch_once(
    client: httpx.AsyncClient, url: str, max_bytes: int
) -> tuple[int, bytes | None, str]:
    """单次 GET；返回 (状态码, 响应体, Location)。异常返回 (0, None, "")。"""
    try:
        async with client.stream("GET", url) as response:
            status = int(getattr(response, "status_code", 0) or 0)
            location = _header(response, "location")
            if status in _REDIRECT_STATUSES or status >= 400:
                return status, None, location
            buffer = bytearray()
            async for chunk in response.aiter_bytes():
                buffer.extend(chunk)
                if len(buffer) > max_bytes:
                    logger.warning("[AIAgent] 图片超过 %d 字节上限，已跳过 %.80s", max_bytes, url)
                    return status, None, ""
            return status, bytes(buffer), ""
    except Exception as e:
        logger.warning("[AIAgent] 图片下载异常 %.80s: %s", url, e)
        return 0, None, ""


async def _download_image(
    client: httpx.AsyncClient, url: str, max_bytes: int
) -> tuple[bytes, str] | None:
    """下载图片并识别真实格式，逐跳校验重定向目标。"""
    target = url
    for _ in range(_MAX_REDIRECTS + 1):
        host = (urlparse(target).hostname or "").strip().lower().rstrip(".")
        if not await _host_is_allowed(host):
            logger.warning("[AIAgent] 图片地址指向受限主机，已跳过 %.80s", target)
            return None

        status, payload, location = await _fetch_once(client, target, max_bytes)
        if status in _REDIRECT_STATUSES:
            if not location:
                logger.warning("[AIAgent] 图片重定向缺少 Location，已跳过 %.80s", target)
                return None
            target = urljoin(target, location)
            if not _is_safe_url(target):
                logger.warning("[AIAgent] 图片重定向到不允许的地址，已跳过 %.80s", target)
                return None
            continue
        if status == 0:
            return None
        if status >= 400:
            logger.warning("[AIAgent] 图片下载失败 HTTP %s %.80s", status, target)
            return None
        if payload is None:
            return None

        mime = _sniff_mime(payload)
        if not mime:
            logger.warning("[AIAgent] 不支持的图片格式，已跳过 %.80s", target)
            return None
        return payload, mime

    logger.warning("[AIAgent] 图片重定向次数过多，已跳过 %.80s", url)
    return None


def _prepare_image(raw: bytes, mime: str) -> tuple[bytes, str]:
    """把过大的图片缩到模型友好的尺寸；动图或解码失败的原样返回。

    模型会把图片缩到总像素约 1300×1300，同时 API 限制单边 8192px、
    单图 32 MiB，所以这里提前把长边压到 2048px、体积压到 3 MiB 以内。
    """
    if mime == "image/gif":
        return raw, mime
    try:
        from PIL import Image, ImageOps
    except Exception:  # Pillow 不可用时退回原图，交给模型侧自己缩放
        return raw, mime

    try:
        with Image.open(io.BytesIO(raw)) as image:
            if getattr(image, "is_animated", False):
                return raw, mime
            if max(image.size) <= _MAX_EDGE and len(raw) <= _PASSTHROUGH_BYTES:
                return raw, mime

            image = ImageOps.exif_transpose(image)
            resized = max(image.size) > _MAX_EDGE
            if resized:
                image.thumbnail((_MAX_EDGE, _MAX_EDGE), Image.Resampling.LANCZOS)
            encoded, encoded_mime = _encode_image(image, mime)
    except Exception as e:
        logger.debug("[AIAgent] 图片预处理失败，按原样发送: %s", e)
        return raw, mime

    # 只是为了压体积才重编码、结果反而更大时，保留原图。
    if not resized and len(encoded) >= len(raw):
        return raw, mime
    return encoded, encoded_mime


def _encode_image(image: Any, mime: str) -> tuple[bytes, str]:
    """按原格式重新编码；PNG 压不下来时改用 WebP。"""
    from PIL import Image

    if mime == "image/png":
        buffer = io.BytesIO()
        if image.mode not in ("RGB", "RGBA", "L", "LA", "P"):
            image = image.convert("RGBA")
        image.save(buffer, "PNG", compress_level=6)
        encoded = buffer.getvalue()
        if len(encoded) <= _PASSTHROUGH_BYTES:
            return encoded, "image/png"
        buffer = io.BytesIO()
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA")
        image.save(buffer, "WEBP", quality=90, method=4)
        return buffer.getvalue(), "image/webp"

    if mime == "image/webp":
        buffer = io.BytesIO()
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA")
        image.save(buffer, "WEBP", quality=90, method=4)
        return buffer.getvalue(), "image/webp"

    # JPEG 兜底：透明图先拍平到白底，否则直接 save 会报错。
    if image.mode not in ("RGB", "L"):
        flattened = Image.new("RGB", image.size, (255, 255, 255))
        rgba = image.convert("RGBA")
        flattened.paste(rgba, mask=rgba.getchannel("A"))
        image = flattened
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=88, optimize=True)
    return buffer.getvalue(), "image/jpeg"



def _sniff_mime(raw: bytes) -> str:
    for signature, mime in _MIME_SIGNATURES:
        if raw.startswith(signature):
            return mime
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return ""
