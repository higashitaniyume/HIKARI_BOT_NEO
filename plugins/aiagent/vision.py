"""给 AI Agent 加「眼睛」：把 QQ 消息里的图片转成模型能吃的图片块。

DeepSeek 的 deepseek-v4-flash-vision-exp 支持图片输入
（https://api-docs.deepseek.com/guides/vision/）：

- Chat Completions：content 变成块数组，图片块是
  ``{"type": "image_url", "image_url": {"url": ..., "detail": ...}}``；
- Responses API：图片块是 ``{"type": "input_image", "image_url": <字符串>,
  "detail": ...}``，文本块要写成 ``input_text``；
- 只有 user 消息能带图片，system / assistant 带图片会 400；
- detail=low 会把图片缩到 512×512，每张图最多按 384 token 计费。

QQ 图床直链带鉴权参数且时效很短，模型侧未必拉得到，所以这里先本地下载再转成
base64 data URI 发出去。格式按字节头识别（文档明确说不看文件名和 MIME 声明）。
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

import httpx

from .utils import safe_int

logger = logging.getLogger("HikariBot.AIAgent.Vision")

VISION_DETAILS = ("low", "high", "original", "auto")

_MIME_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def vision_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    section = cfg.get("vision") if isinstance(cfg.get("vision"), dict) else {}
    detail = str(section.get("detail") or "low").strip().lower()
    return {
        "enabled": bool(section.get("enabled", False)),
        "max_images": safe_int(section.get("max_images"), 2, minimum=1, maximum=8),
        "detail": detail if detail in VISION_DETAILS else "low",
        "include_quoted": bool(section.get("include_quoted", True)),
        "max_bytes": safe_int(section.get("max_bytes"), 5_242_880, minimum=65536, maximum=33_554_432),
        "download_timeout_seconds": safe_int(
            section.get("download_timeout_seconds"), 20, minimum=3, maximum=120
        ),
    }


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
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        downloads = await asyncio.gather(
            *(_download_data_uri(client, url, settings["max_bytes"]) for url in urls)
        )
    return [
        {"type": "image_url", "image_url": {"url": data_uri, "detail": settings["detail"]}}
        for data_uri in downloads
        if data_uri
    ]


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
        if value.startswith(("http://", "https://")):
            return value
    return ""


async def _download_data_uri(client: httpx.AsyncClient, url: str, max_bytes: int) -> str:
    try:
        async with client.stream("GET", url) as response:
            if response.status_code >= 400:
                logger.warning("[AIAgent] 图片下载失败 HTTP %s %.80s", response.status_code, url)
                return ""
            buffer = bytearray()
            async for chunk in response.aiter_bytes():
                buffer.extend(chunk)
                if len(buffer) > max_bytes:
                    logger.warning("[AIAgent] 图片超过 %d 字节上限，已跳过 %.80s", max_bytes, url)
                    return ""
    except Exception as e:
        logger.warning("[AIAgent] 图片下载异常 %.80s: %s", url, e)
        return ""

    mime = _sniff_mime(bytes(buffer))
    if not mime:
        logger.warning("[AIAgent] 不支持的图片格式，已跳过 %.80s", url)
        return ""
    return f"data:{mime};base64,{base64.b64encode(buffer).decode('ascii')}"


def _sniff_mime(raw: bytes) -> str:
    for signature, mime in _MIME_SIGNATURES:
        if raw.startswith(signature):
            return mime
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return ""
