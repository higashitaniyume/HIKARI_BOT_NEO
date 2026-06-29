"""
YouTube 视频下载模块。

yt-dlp 的 Python API 是同步的；这里通过 asyncio.to_thread 包装，避免阻塞
NoneBot 事件循环。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import deno
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError, MaxDownloadsReached

logger = logging.getLogger("HikariBot.YouTubeDownloader")

VIDEO_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}


class YouTubeDownloadError(RuntimeError):
    """YouTube 下载失败或被配置限制拦截。"""


@dataclass(slots=True)
class YouTubeDownloadResult:
    path: Path
    title: str
    uploader: str
    duration: int
    webpage_url: str
    video_id: str
    filesize: int


def file_as_uri(path: Path) -> str:
    """将本地路径转为 file:// URI。"""
    return path.resolve().as_uri()


async def download_youtube_video(url: str, cfg: dict[str, Any]) -> YouTubeDownloadResult:
    """异步下载 YouTube 视频。"""
    return await asyncio.to_thread(_download_youtube_video_sync, url, cfg)


def _download_youtube_video_sync(url: str, cfg: dict[str, Any]) -> YouTubeDownloadResult:
    _ensure_deno_on_path()

    max_file_mb = max(1, int(cfg.get("max_file_mb", 1024)))
    max_bytes = max_file_mb * 1024 * 1024
    max_height = max(144, int(cfg.get("max_height", 720)))
    cache_dir = Path(str(cfg.get("cache_dir") or "/tmp/hikari_bot/youtube_downloader"))
    download_timeout = max(60, int(cfg.get("download_timeout", 1800)))
    socket_timeout = max(5, int(cfg.get("socket_timeout", 30)))
    retries = max(0, int(cfg.get("retries", 5)))

    cache_dir.mkdir(parents=True, exist_ok=True)

    t_start = time.time()
    info_opts = _build_ydl_opts(
        cfg,
        download=False,
        max_bytes=max_bytes,
        max_height=max_height,
        socket_timeout=socket_timeout,
        retries=retries,
    )

    try:
        with YoutubeDL(info_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except (DownloadError, ExtractorError, MaxDownloadsReached) as e:
        raise YouTubeDownloadError(_friendly_error(str(e))) from e

    if not isinstance(info, dict):
        raise YouTubeDownloadError("无法读取视频信息。")
    if info.get("_type") == "playlist":
        raise YouTubeDownloadError("暂不支持播放列表，请发送单个视频链接。")

    title = str(info.get("title") or "YouTube Video")
    uploader = str(info.get("uploader") or info.get("channel") or "Unknown")
    duration = int(info.get("duration") or 0)
    video_id = str(info.get("id") or hashlib.sha256(url.encode("utf-8")).hexdigest()[:16])
    webpage_url = str(info.get("webpage_url") or url)
    live_status = str(info.get("live_status") or "")

    if info.get("is_live") or live_status in {"is_live", "is_upcoming"}:
        raise YouTubeDownloadError("直播或未开始的视频暂不下载。")

    existing = _find_cached_file(cache_dir, video_id, max_height, max_bytes)
    if existing:
        logger.info("[YouTube] 缓存命中 -> %s", existing.name)
        return YouTubeDownloadResult(
            path=existing,
            title=title,
            uploader=uploader,
            duration=duration,
            webpage_url=webpage_url,
            video_id=video_id,
            filesize=existing.stat().st_size,
        )

    work_dir = cache_dir / "tmp" / f"youtube_{uuid.uuid4().hex}"
    work_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(work_dir / "%(id)s.%(ext)s")

    download_opts = _build_ydl_opts(
        cfg,
        download=True,
        max_bytes=max_bytes,
        max_height=max_height,
        socket_timeout=socket_timeout,
        retries=retries,
        outtmpl=outtmpl,
    )

    logger.info(
        "[YouTube] 开始下载 -> id=%s, title=%s, max_height=%sp, max_file=%sMB",
        video_id,
        title[:80],
        max_height,
        max_file_mb,
    )

    try:
        _download_with_ytdlp_subprocess(url, download_opts, download_timeout)
        candidate = _select_downloaded_file(work_dir)
        if candidate is None:
            raise YouTubeDownloadError("下载完成但没有找到视频文件。")

        filesize = candidate.stat().st_size
        if filesize > max_bytes:
            raise YouTubeDownloadError(f"视频超过大小限制：{filesize / 1024 / 1024:.1f}MB。")

        suffix = candidate.suffix.lower() if candidate.suffix.lower() in VIDEO_SUFFIXES else ".mp4"
        final_path = cache_dir / f"youtube_{video_id}_{max_height}p{suffix}"
        if final_path.exists():
            final_path.unlink()
        shutil.move(str(candidate), final_path)
    except YouTubeDownloadError:
        raise
    except Exception as e:
        raise YouTubeDownloadError(_friendly_error(str(e))) from e
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    elapsed = time.time() - t_start
    logger.info(
        "[YouTube] 下载完成 -> id=%s, file=%s, size=%.1fMB, elapsed=%.2fs",
        video_id,
        final_path.name,
        final_path.stat().st_size / 1024 / 1024,
        elapsed,
    )

    return YouTubeDownloadResult(
        path=final_path,
        title=title,
        uploader=uploader,
        duration=duration,
        webpage_url=webpage_url,
        video_id=video_id,
        filesize=final_path.stat().st_size,
    )


def _build_ydl_opts(
    cfg: dict[str, Any],
    *,
    download: bool,
    max_bytes: int,
    max_height: int,
    socket_timeout: int,
    retries: int,
    outtmpl: str | None = None,
) -> dict[str, Any]:
    selected_format = str(cfg.get("format") or "").strip()
    if not selected_format:
        selected_format = (
            f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/"
            f"b[height<={max_height}][ext=mp4]/"
            f"bv*[height<={max_height}]+ba/"
            f"b[height<={max_height}]/best[height<={max_height}]/best"
        )

    opts: dict[str, Any] = {
        "format": selected_format,
        "merge_output_format": "mp4",
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
    if download and outtmpl:
        opts["outtmpl"] = outtmpl

    return opts


def _ensure_deno_on_path() -> None:
    try:
        deno_bin = Path(deno.find_deno_bin())
    except Exception as e:
        logger.warning("[YouTube] Deno 可执行文件定位失败，yt-dlp 将自行尝试: %s", e)
        return

    deno_dir = str(deno_bin.parent)
    path_parts = os.environ.get("PATH", "").split(os.pathsep)
    if deno_dir not in path_parts:
        os.environ["PATH"] = deno_dir + os.pathsep + os.environ.get("PATH", "")


def _download_with_ytdlp_subprocess(url: str, opts: dict[str, Any], timeout: int) -> None:
    args = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--quiet",
        "--no-warnings",
        "--windows-filenames",
        "--force-overwrites",
        "--continue",
        "--format",
        str(opts["format"]),
        "--merge-output-format",
        "mp4",
        "--max-filesize",
        str(opts["max_filesize"]),
        "--socket-timeout",
        str(opts["socket_timeout"]),
        "--retries",
        str(opts["retries"]),
        "--fragment-retries",
        str(opts["fragment_retries"]),
        "--output",
        str(opts["outtmpl"]),
    ]

    if opts.get("cookiefile"):
        args.extend(["--cookies", str(opts["cookiefile"])])

    args.append(url)

    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise YouTubeDownloadError("下载超时，请稍后再试。") from e

    if proc.returncode != 0:
        message = proc.stderr.strip() or proc.stdout.strip() or "yt-dlp 下载失败。"
        raise YouTubeDownloadError(_friendly_error(message))


def _find_cached_file(cache_dir: Path, video_id: str, max_height: int, max_bytes: int) -> Path | None:
    for path in cache_dir.glob(f"youtube_{video_id}_{max_height}p.*"):
        if path.suffix.lower() not in VIDEO_SUFFIXES:
            continue
        if path.stat().st_size <= 0:
            continue
        if path.stat().st_size > max_bytes:
            path.unlink(missing_ok=True)
            continue
        return path
    return None


def _select_downloaded_file(work_dir: Path) -> Path | None:
    candidates = [
        path
        for path in work_dir.rglob("*")
        if path.is_file()
        and path.suffix.lower() in VIDEO_SUFFIXES
        and not path.name.endswith(".part")
        and path.stat().st_size > 0
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_size)


def _friendly_error(error: str) -> str:
    lower = error.lower()
    if "file is larger than max-filesize" in lower or "larger than max-filesize" in lower:
        return "视频超过大小限制。"
    if "private video" in lower:
        return "这是私密视频，无法下载。"
    if "sign in" in lower or "cookies" in lower:
        return "YouTube 要求登录验证；可在插件配置里提供 cookiefile 后重试。"
    if "unavailable" in lower:
        return "视频当前不可用。"
    if not error.strip():
        return "下载失败。"
    return error.strip()[:180]
