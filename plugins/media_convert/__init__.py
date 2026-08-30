"""动图 ⇄ MP4 互转：引用媒体消息后回复「转mp4/转视频」「转gif/转动图/转贴纸」触发。"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from PIL import Image

from core.activity_tracker import finish_activity, start_activity
from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command
from core.temp_media_cleaner import register_temp_media_path

from plugins.media_transcoder import (
    StickerGifOptions,
    TranscodeError,
    ensure_sticker_gif,
    run_cmd,
)

from .config import get_config

logger = logging.getLogger("HikariBot.MediaConvert")

# 与 NapCat 容器相同的路径（docker-compose 把 NapCat 临时目录按原路径只读挂进 bot 容器），
# 视频段无 url 时可直接复用 NapCat 落地的本地文件。
_NAPCAT_TEMP_ROOT = Path("/app/.config/QQ/NapCat/temp")

_IMAGE_EXTS = {".gif", ".jpg", ".jpeg", ".png", ".webp", ".apng"}
_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}

# 同一时间最多两个转换任务，避免 ffmpeg 并发打爆磁盘/CPU
_sem = asyncio.Semaphore(2)


class MediaTooLargeError(RuntimeError):
    """媒体超过方向对应的大小上限。"""

    def __init__(self, size_mb: float, limit_mb: int) -> None:
        super().__init__(f"媒体 {size_mb:.1f}MB 超过上限 {limit_mb}MB")
        self.size_mb = size_mb
        self.limit_mb = limit_mb


@dataclass(slots=True)
class ResolvedMedia:
    path: Path
    owned: bool


# 「贴纸」在本项目里等价于 GIF（见 media_transcoder：进入本地贴纸包必须是 GIF），
# 所以「转贴纸」和「转动图」都指向视频 → GIF 方向。
@command(
    "转mp4",
    aliases=("转MP4", "转视频"),
    description="把引用的动图转换为 MP4",
    usage="引用动图消息后回复 转mp4 / 转视频",
    detail_key="media_convert.help",
    category="媒体",
)
async def cmd_to_mp4(ctx: CommandContext) -> None:
    await _handle_convert(ctx, to_gif=False)


@command(
    "转gif",
    aliases=("转GIF", "转动图", "转贴纸"),
    description="把引用的视频转换为 GIF",
    usage="引用视频消息后回复 转gif / 转动图 / 转贴纸",
    detail_key="media_convert.help",
    category="媒体",
)
async def cmd_to_gif(ctx: CommandContext) -> None:
    await _handle_convert(ctx, to_gif=True)


async def _handle_convert(ctx: CommandContext, *, to_gif: bool) -> None:
    cfg = get_config()
    if not cfg.get("enabled", True):
        logger.info(
            "[MediaConvert] 插件已关闭，忽略 %s 命令",
            getattr(ctx, "matched", "") or ("转gif" if to_gif else "转mp4"),
        )
        return

    try:
        async with _sem:
            await _run_conversion(ctx, cfg, to_gif=to_gif)
    except Exception as e:
        logger.exception("[MediaConvert] 转换处理异常: %s", e)
        try:
            await ctx.send(msg("media_convert.failed"))
        except Exception:
            logger.exception("[MediaConvert] 发送失败提示失败")


async def _run_conversion(ctx: CommandContext, cfg: dict[str, Any], *, to_gif: bool) -> None:
    dest_dir = Path(str(cfg.get("temp_root"))).expanduser()
    dest_dir.mkdir(parents=True, exist_ok=True)

    segments = await _collect_reply_segments(ctx.bot, ctx.event)
    if to_gif:
        await _run_to_gif(ctx, cfg, segments, dest_dir)
    else:
        await _run_to_mp4(ctx, cfg, segments, dest_dir)


async def _run_to_mp4(
    ctx: CommandContext,
    cfg: dict[str, Any],
    segments: list[dict[str, Any]],
    dest_dir: Path,
) -> None:
    seg = _pick_first(segments, "image")
    if seg is None:
        if _has_type(segments, "video"):
            await ctx.send(msg("media_convert.use_convert_gif"))
            return
        await ctx.send(msg("media_convert.usage"))
        return

    resolved = await _resolve_media(seg, kind="image", dest_dir=dest_dir, cfg=cfg)
    if resolved is None:
        await ctx.send(msg("media_convert.media_unavailable"))
        return

    aid = start_activity(
        "media_convert",
        "converting",
        "动图转 MP4",
        description=resolved.path.name,
    )
    try:
        if not await _is_animated_image(resolved.path):
            await ctx.send(msg("media_convert.not_animated"))
            return

        output_path = dest_dir / f"convert_{uuid.uuid4().hex}.mp4"
        await _gif_to_mp4(resolved.path, output_path, timeout_seconds=int(cfg["ffmpeg_timeout_seconds"]))
        register_temp_media_path(output_path, ttl_seconds=int(cfg["output_ttl_seconds"]))
        await ctx.send(Message(MessageSegment.video(output_path.resolve().as_uri())))
    finally:
        finish_activity(aid)
        _delete_owned(resolved)


async def _run_to_gif(
    ctx: CommandContext,
    cfg: dict[str, Any],
    segments: list[dict[str, Any]],
    dest_dir: Path,
) -> None:
    seg = _pick_first(segments, "video")
    if seg is None:
        image_seg = _pick_first(segments, "image")
        if image_seg is None:
            await ctx.send(msg("media_convert.usage"))
            return
        resolved = await _resolve_media(image_seg, kind="image", dest_dir=dest_dir, cfg=cfg)
        if resolved is None:
            await ctx.send(msg("media_convert.media_unavailable"))
            return
        try:
            animated = await _is_animated_image(resolved.path)
        finally:
            _delete_owned(resolved)
        await ctx.send(msg("media_convert.already_gif" if animated else "media_convert.not_animated"))
        return

    try:
        resolved = await _resolve_media(seg, kind="video", dest_dir=dest_dir, cfg=cfg)
    except MediaTooLargeError:
        await ctx.send(msg("media_convert.video_too_large", max_mb=int(cfg["max_video_mb"])))
        return
    if resolved is None:
        await ctx.send(msg("media_convert.media_unavailable"))
        return

    aid = start_activity(
        "media_convert",
        "converting",
        "视频转 GIF",
        description=resolved.path.name,
    )
    try:
        output_path = dest_dir / f"convert_{uuid.uuid4().hex}.gif"
        options = StickerGifOptions(
            fps=int(cfg["gif_fps"]),
            width=int(cfg["gif_width"]),
            max_colors=int(cfg["gif_max_colors"]),
            dither="sierra2_4a",
            tgs_converter_cmd=["uv", "run", "lottie_convert.py"],
        )
        try:
            await ensure_sticker_gif(resolved.path, output_path, options=options)
        except TranscodeError as e:
            raise RuntimeError(f"视频转 GIF 失败: {e}") from e
        register_temp_media_path(output_path, ttl_seconds=int(cfg["output_ttl_seconds"]))
        await ctx.send(Message(MessageSegment.image(output_path.resolve().as_uri())))
    finally:
        finish_activity(aid)
        _delete_owned(resolved)


async def _gif_to_mp4(input_path: Path, output_path: Path, *, timeout_seconds: int) -> None:
    """动图 → MP4。yuv420p 要求偶数尺寸，pad 兜底奇数宽高的动图。"""
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-vf",
        "pad=ceil(iw/2)*2:ceil(ih/2)*2",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    await run_cmd(cmd, timeout=max(30, int(timeout_seconds)))


async def _is_animated_image(path: Path) -> bool:
    def _probe() -> bool:
        try:
            with Image.open(path) as img:
                frames = int(getattr(img, "n_frames", 1) or 1)
                return bool(getattr(img, "is_animated", False)) or frames > 1
        except Exception:
            return False

    return await asyncio.to_thread(_probe)


def _pick_first(segments: list[dict[str, Any]], seg_type: str) -> dict[str, Any] | None:
    for seg in segments:
        if str(seg.get("type") or "") == seg_type:
            return seg
    return None


def _has_type(segments: list[dict[str, Any]], seg_type: str) -> bool:
    return _pick_first(segments, seg_type) is not None


def _reply_from_event(event: Any) -> tuple[Message | None, str]:
    reply = getattr(event, "reply", None)
    message = getattr(reply, "message", None) if reply is not None else None
    mid = str(getattr(reply, "message_id", "") or "") if reply is not None else ""

    if not mid:
        for seg in getattr(event, "message", ()) or ():
            if str(getattr(seg, "type", "") or "") == "reply":
                data = getattr(seg, "data", {}) or {}
                mid = str(data.get("id") or "")
                break
    return message if isinstance(message, Message) else None, mid


def _segment_dicts_from_message(message: Message) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for seg in message:
        seg_type = str(getattr(seg, "type", "") or "")
        if seg_type == "reply":
            continue
        segments.append({"type": seg_type, "data": dict(getattr(seg, "data", {}) or {})})
    return segments


def _segment_dicts_from_api_payload(payload: Any) -> list[dict[str, Any]] | None:
    data = payload
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        data = payload["data"]
    if not isinstance(data, dict):
        return None

    raw = data.get("message")
    if isinstance(raw, list):
        segments: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, dict):
                seg_type = str(item.get("type") or "")
                if seg_type == "reply":
                    continue
                segments.append(
                    {"type": seg_type, "data": dict(item.get("data") or {})}
                )
        return segments
    if isinstance(raw, str) and raw.strip():
        try:
            return _segment_dicts_from_message(Message(raw.strip()))
        except Exception:
            return None
    return None


async def _collect_reply_segments(bot: Any, event: Any) -> list[dict[str, Any]]:
    message, mid = _reply_from_event(event)

    if message is not None:
        segments = _segment_dicts_from_message(message)
        if any(str(seg.get("type") or "") in {"image", "video"} for seg in segments):
            return segments

    if not mid:
        return []

    try:
        payload = await bot.call_api("get_msg", message_id=int(mid))
    except Exception as e:
        logger.warning("[MediaConvert] 回查引用消息失败 id=%s -> %s", mid, e)
        return []

    segments = _segment_dicts_from_api_payload(payload)
    if segments and any(str(seg.get("type") or "") in {"image", "video"} for seg in segments):
        return segments
    if message is not None:
        return _segment_dicts_from_message(message)
    return segments or []


def _local_file_candidates(seg_data: dict[str, Any]) -> list[Path]:
    raw = str(seg_data.get("file") or "").strip()
    if not raw or raw.startswith(("http://", "https://")) or raw.endswith(".image"):
        return []

    candidates: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        key = str(path)
        if key not in seen:
            seen.add(key)
            candidates.append(path)

    path = Path(raw)
    _add(path)
    if not path.is_absolute():
        _add(_NAPCAT_TEMP_ROOT / raw)
    return candidates


def _guess_suffix(*names: str, content_type: str = "", fallback: str) -> str:
    allowed = _IMAGE_EXTS | _VIDEO_EXTS
    for name in names:
        suffix = Path(name).suffix.lower()
        if suffix in allowed:
            return suffix
    if content_type:
        guessed = (mimetypes.guess_extension(content_type.split(";", 1)[0].strip()) or "").lower()
        if guessed == ".jpe":
            guessed = ".jpg"
        if guessed in allowed:
            return guessed
    return fallback


async def _download_media(
    url: str,
    dest: Path,
    *,
    timeout_seconds: float,
    max_bytes: int,
    limit_mb: int,
) -> str:
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
        tmp_path = dest.with_suffix(dest.suffix + ".part")
        tmp_path.unlink(missing_ok=True)
        try:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                content_length_bytes = (
                    int(content_length) if content_length and content_length.isdigit() else 0
                )
                if content_length_bytes > max_bytes:
                    raise MediaTooLargeError(content_length_bytes / 1024 / 1024, limit_mb)

                written = 0
                with tmp_path.open("wb") as f:
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                        written += len(chunk)
                        if written > max_bytes:
                            raise MediaTooLargeError(written / 1024 / 1024, limit_mb)
                        f.write(chunk)
                tmp_path.replace(dest)
                return response.headers.get("content-type", "")
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise


async def _resolve_media(
    seg: dict[str, Any],
    *,
    kind: str,
    dest_dir: Path,
    cfg: dict[str, Any],
) -> ResolvedMedia | None:
    seg_data = dict(seg.get("data") or {})
    limit_mb = int(cfg.get("max_image_mb" if kind == "image" else "max_video_mb", 50))
    max_bytes = max(limit_mb, 1) * 1024 * 1024

    url = str(seg_data.get("url") or "").strip()
    if url.startswith(("http://", "https://")):
        suffix = _guess_suffix(
            str(seg_data.get("file") or ""),
            url,
            content_type="",
            fallback=".gif" if kind == "image" else ".mp4",
        )
        dest = dest_dir / f"in_{uuid.uuid4().hex}{suffix}"
        try:
            await _download_media(
                url,
                dest,
                timeout_seconds=float(cfg.get("download_timeout_seconds", 60)),
                max_bytes=max_bytes,
                limit_mb=limit_mb,
            )
            return ResolvedMedia(path=dest, owned=True)
        except MediaTooLargeError:
            raise
        except Exception as e:
            logger.warning("[MediaConvert] 媒体下载失败 url=%s -> %s", url, e)

    for candidate in _local_file_candidates(seg_data):
        try:
            if not candidate.is_file():
                continue
            size = candidate.stat().st_size
        except OSError:
            continue
        if size > max_bytes:
            raise MediaTooLargeError(size / 1024 / 1024, limit_mb)
        logger.info("[MediaConvert] 复用本地媒体文件: %s", candidate)
        return ResolvedMedia(path=candidate, owned=False)

    return None


def _delete_owned(resolved: ResolvedMedia) -> None:
    if not resolved.owned:
        return
    try:
        resolved.path.unlink(missing_ok=True)
    except OSError as e:
        logger.warning("[MediaConvert] 删除临时输入失败 path=%s -> %s", resolved.path, e)
