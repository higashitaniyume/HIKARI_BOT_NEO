"""
SoundCloud 音频下载模块。

yt-dlp 的 Python API 是同步的；这里通过 asyncio.to_thread 包装，避免阻塞
NoneBot 事件循环。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError

from core.temp_media_cleaner import DEFAULT_TEMP_MEDIA_TTL_SECONDS, register_temp_media_path, ttl_seconds_from_config

logger = logging.getLogger("HikariBot.SoundCloudDownloader")

AUDIO_SUFFIXES = {".m4a", ".mp3", ".opus", ".webm", ".aac", ".ogg", ".wav", ".flac"}


class SoundCloudDownloadError(RuntimeError):
    """SoundCloud 下载失败或被配置限制拦截。"""


@dataclass(slots=True)
class SoundCloudDownloadResult:
    path: Path
    title: str
    uploader: str
    duration: int
    webpage_url: str
    track_id: str
    filesize: int


def file_as_uri(path: Path) -> str:
    """将本地路径转为 file:// URI。"""
    return path.resolve().as_uri()


async def download_soundcloud_track(url: str, cfg: dict[str, Any]) -> SoundCloudDownloadResult:
    """异步下载 SoundCloud 音频。"""
    return await asyncio.to_thread(_download_soundcloud_track_sync, url, cfg)


def _download_soundcloud_track_sync(url: str, cfg: dict[str, Any]) -> SoundCloudDownloadResult:
    max_file_mb = max(1, int(cfg.get("max_file_mb", 50)))
    max_bytes = max_file_mb * 1024 * 1024
    cache_ttl_seconds = ttl_seconds_from_config(
        cfg.get("cache_ttl_seconds"),
        DEFAULT_TEMP_MEDIA_TTL_SECONDS,
    )
    cache_dir = Path(str(cfg.get("cache_dir") or "/tmp/hikari_bot/soundcloud"))
    download_timeout = max(60, int(cfg.get("download_timeout", 600)))
    socket_timeout = max(5, int(cfg.get("socket_timeout", 30)))
    retries = max(0, int(cfg.get("retries", 3)))
    preferred_codec = str(cfg.get("preferred_codec", "m4a")).strip()

    cache_dir.mkdir(parents=True, exist_ok=True)

    # 步骤 1: 提取元数据
    t_start = time.time()
    info_opts = _build_ydl_opts(
        cfg,
        download=False,
        max_bytes=max_bytes,
        socket_timeout=socket_timeout,
        retries=retries,
    )

    try:
        with YoutubeDL(info_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except (DownloadError, ExtractorError) as e:
        raise SoundCloudDownloadError(_friendly_error(str(e))) from e

    if not isinstance(info, dict):
        raise SoundCloudDownloadError("无法读取音频信息。")
    if info.get("_type") == "playlist" or info.get("_type") == "set":
        raise SoundCloudDownloadError("暂不支持歌单/合集，请发送单个音频链接。")

    title = str(info.get("title") or "SoundCloud Audio")
    uploader = str(info.get("uploader") or info.get("creator") or "Unknown")
    duration = int(info.get("duration") or 0)
    track_id = str(info.get("id") or hashlib.sha256(url.encode("utf-8")).hexdigest()[:16])
    webpage_url = str(info.get("webpage_url") or url)

    # 步骤 2: 检查缓存
    cache_key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    existing = _find_cached_file(cache_dir, cache_key, max_bytes)
    if existing:
        register_temp_media_path(existing, ttl_seconds=cache_ttl_seconds)
        logger.info("[SoundCloud] 缓存命中 -> %s", existing.name)
        return SoundCloudDownloadResult(
            path=existing,
            title=title,
            uploader=uploader,
            duration=duration,
            webpage_url=webpage_url,
            track_id=track_id,
            filesize=existing.stat().st_size,
        )

    # 步骤 3: 下载
    work_dir = cache_dir / "tmp" / f"soundcloud_{uuid.uuid4().hex}"
    work_dir.mkdir(parents=True, exist_ok=True)

    codec = preferred_codec if preferred_codec in ("m4a", "mp3", "opus", "flac", "best") else "m4a"
    outtmpl = str(work_dir / "%(id)s.%(ext)s")

    download_opts = _build_ydl_opts(
        cfg,
        download=True,
        max_bytes=max_bytes,
        socket_timeout=socket_timeout,
        retries=retries,
        outtmpl=outtmpl,
        codec=codec,
    )

    logger.info(
        "[SoundCloud] 开始下载 -> id=%s, title=%s, codec=%s",
        track_id,
        title[:80],
        codec,
    )

    try:
        with YoutubeDL(download_opts) as ydl:
            ydl.download([url])

        candidate = _select_downloaded_file(work_dir)
        if candidate is None:
            raise SoundCloudDownloadError("下载完成但没有找到音频文件。")

        filesize = candidate.stat().st_size
        if filesize > max_bytes:
            raise SoundCloudDownloadError(f"音频超过大小限制：{filesize / 1024 / 1024:.1f}MB。")

        suffix = candidate.suffix.lower() if candidate.suffix.lower() in AUDIO_SUFFIXES else ".m4a"
        final_path = cache_dir / f"soundcloud_{cache_key}{suffix}"
        if final_path.exists():
            final_path.unlink()
        shutil.move(str(candidate), final_path)
    except SoundCloudDownloadError:
        raise
    except Exception as e:
        raise SoundCloudDownloadError(_friendly_error(str(e))) from e
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    elapsed = time.time() - t_start
    logger.info(
        "[SoundCloud] 下载完成 -> id=%s, file=%s, size=%.1fMB, elapsed=%.2fs",
        track_id,
        final_path.name,
        final_path.stat().st_size / 1024 / 1024,
        elapsed,
    )
    register_temp_media_path(final_path, ttl_seconds=cache_ttl_seconds)

    return SoundCloudDownloadResult(
        path=final_path,
        title=title,
        uploader=uploader,
        duration=duration,
        webpage_url=webpage_url,
        track_id=track_id,
        filesize=final_path.stat().st_size,
    )


def _build_ydl_opts(
    cfg: dict[str, Any],
    *,
    download: bool,
    max_bytes: int,
    socket_timeout: int,
    retries: int,
    outtmpl: str | None = None,
    codec: str = "m4a",
) -> dict[str, Any]:
    """构建 yt-dlp 选项。"""
    if codec == "best":
        fmt = "bestaudio/best"
    else:
        fmt = f"bestaudio[ext={codec}]/bestaudio/best"

    opts: dict[str, Any] = {
        "format": fmt,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": max_bytes,
        "socket_timeout": socket_timeout,
        "retries": retries,
        "fragment_retries": retries,
        "ignoreerrors": False,
        "overwrites": True,
        "continuedl": True,
        "windowsfilenames": True,
    }

    cookiefile = str(cfg.get("cookiefile") or "").strip()
    if cookiefile:
        opts["cookiefile"] = cookiefile

    if codec != "best" and download:
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": codec,
            }
        ]

    if download and outtmpl:
        opts["outtmpl"] = outtmpl

    return opts


def _find_cached_file(cache_dir: Path, cache_key: str, max_bytes: int) -> Path | None:
    """在缓存目录中查找已下载的文件。"""
    for path in cache_dir.glob(f"soundcloud_{cache_key}.*"):
        if path.suffix.lower() not in AUDIO_SUFFIXES:
            continue
        if path.stat().st_size <= 0:
            continue
        if path.stat().st_size > max_bytes:
            path.unlink(missing_ok=True)
            continue
        return path
    return None


def _select_downloaded_file(work_dir: Path) -> Path | None:
    """从工作目录中选择已下载的音频文件。"""
    candidates = [
        path
        for path in work_dir.rglob("*")
        if path.is_file()
        and path.suffix.lower() in AUDIO_SUFFIXES
        and not path.name.endswith(".part")
        and path.stat().st_size > 0
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_size)


def _friendly_error(error: str) -> str:
    """将 yt-dlp 错误信息转为用户友好的中文描述。"""
    lower = error.lower()
    if "file is larger than max-filesize" in lower or "larger than max-filesize" in lower:
        return "音频超过大小限制。"
    if "private" in lower or "not found" in lower:
        return "该音频为私密或未找到，无法下载。"
    if "sign in" in lower or "cookies" in lower:
        return "SoundCloud 要求登录验证；可在插件配置里提供 cookiefile 后重试。"
    if "unavailable" in lower or "copyright" in lower or "blocked" in lower:
        return "该音频当前不可用（版权或区域限制）。"
    if "playlist" in lower or "set" in lower:
        return "暂不支持歌单/合集，请发送单个音频链接。"
    if not error.strip():
        return "下载失败。"
    return error.strip()[:180]
