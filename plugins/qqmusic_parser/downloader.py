"""
QQ 音乐音频下载模块（yt-dlp）。

yt-dlp 的 Python API 是同步的，这里用 ``asyncio.to_thread`` 包装，避免阻塞
NoneBot 事件循环。

音质档位来自 yt-dlp 的 qqmusic 提取器，格式 ID 即 ``flac / ape / 320mp3 /
128mp3 / 96aac / 48aac``。匿名请求只会返回后三档；带上登录 cookie 后
320mp3 与 flac 才会出现。**不做任何转码**，下载到的就是服务端原档。
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError

from core.temp_media_cleaner import (
    DEFAULT_TEMP_MEDIA_TTL_SECONDS,
    register_temp_media_path,
    ttl_seconds_from_config,
)

from .errors import (
    QQMusicDownloadError,
    QQMusicError,
    QQMusicNoFormatError,
    QQMusicSizeError,
)
from .parser import build_song_url

logger = logging.getLogger("HikariBot.QQMusicDownloader")

AUDIO_SUFFIXES = {".mp3", ".m4a", ".flac", ".ape", ".aac", ".ogg", ".opus", ".wav"}

# yt-dlp qqmusic 提取器实际会产出的格式 ID
KNOWN_FORMATS: tuple[str, ...] = ("flac", "ape", "320mp3", "128mp3", "96aac", "48aac")

# 音质档位 -> 展示用名称
QUALITY_LABELS: dict[str, str] = {
    "flac": "无损 FLAC",
    "ape": "无损 APE",
    "320mp3": "320k MP3",
    "128mp3": "128k MP3（标准音质）",
    "96aac": "96k AAC",
    "48aac": "48k AAC",
}

# yt-dlp 在没有可用格式时给出的措辞（其 "需要登录" 提示对数字 ID 场景是误报）
_NO_FORMAT_HINTS = (
    "requested format is not available",
    "no video formats found",
    "only available for registered users",
    "sign in to confirm",
    "login required",
)


@dataclass(slots=True)
class QQAudioResult:
    """下载成功的音频。"""

    path: Path
    songmid: str
    format_id: str
    title: str
    duration: int
    filesize: int

    @property
    def quality_label(self) -> str:
        return QUALITY_LABELS.get(self.format_id, self.format_id or "未知音质")

    @property
    def ext(self) -> str:
        return self.path.suffix


def file_as_uri(path: Path) -> str:
    """将本地路径转为 file:// URI。"""
    return path.resolve().as_uri()


def format_selector(cfg: dict[str, Any]) -> str:
    """按配置的音质优先级生成 yt-dlp 格式选择串。

    未识别的取值会被丢弃；末尾追加 ``bestaudio/best`` 兜底，保证配置写错时
    仍然尽量下发，而不是直接失败。
    """
    raw = cfg.get("format_priority")
    priority = raw if isinstance(raw, list) else []
    picked = [str(item).strip() for item in priority]
    picked = [item for item in picked if item in KNOWN_FORMATS]

    if not picked:
        picked = ["128mp3", "96aac", "48aac"]

    # 去重并保持顺序
    seen: set[str] = set()
    ordered = [item for item in picked if not (item in seen or seen.add(item))]

    ordered.append("bestaudio")
    ordered.append("best")
    return "/".join(ordered)


async def download_qqmusic_audio(
    songmid: str,
    cfg: dict[str, Any],
) -> QQAudioResult:
    """异步下载 QQ 音乐音频。"""
    return await asyncio.to_thread(_download_sync, songmid, cfg)


def _download_sync(songmid: str, cfg: dict[str, Any]) -> QQAudioResult:
    from .config import get_cookiefile

    max_file_mb = max(1, int(cfg.get("max_file_mb", 200)))
    max_bytes = max_file_mb * 1024 * 1024
    cache_ttl_seconds = ttl_seconds_from_config(
        cfg.get("cache_ttl_seconds"),
        DEFAULT_TEMP_MEDIA_TTL_SECONDS,
    )
    cache_dir = Path(str(cfg.get("cache_dir") or "/tmp/hikari_bot/qqmusic"))
    download_timeout = max(60, int(cfg.get("download_timeout", 600)))
    socket_timeout = max(5, int(cfg.get("socket_timeout", 30)))
    retries = max(0, int(cfg.get("retries", 3)))

    cache_dir.mkdir(parents=True, exist_ok=True)

    cookiefile = get_cookiefile(cfg)
    cookie_arg = str(cookiefile) if cookiefile is not None and cookiefile.is_file() else ""
    if cookiefile is not None and not cookie_arg:
        logger.warning("[QQMusic] cookiefile 不存在，按匿名请求 → %s", cookiefile)

    url = build_song_url(songmid)
    selector = format_selector(cfg)
    t_start = time.time()

    # 步骤 1：解析元数据并完成格式选择（此步就能发现「没有可用格式」）
    info_opts = _build_ydl_opts(
        format_selector=selector,
        max_bytes=max_bytes,
        socket_timeout=socket_timeout,
        retries=retries,
        cookiefile=cookie_arg,
    )
    try:
        with YoutubeDL(info_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except (DownloadError, ExtractorError) as exc:
        raise _map_extract_error(str(exc)) from exc

    if not isinstance(info, dict):
        raise QQMusicError("无法读取歌曲信息。")

    format_id = str(info.get("format_id") or "").strip()
    ext = str(info.get("ext") or "").strip().lstrip(".")
    title = str(info.get("title") or "").strip()
    duration = int(info.get("duration") or 0)
    if not format_id:
        raise QQMusicNoFormatError("该曲目没有可用音质。")

    logger.info(
        "[QQMusic] 已选音质 → songmid=%s, format=%s, ext=%s, login=%s",
        songmid, format_id, ext, "yes" if cookie_arg else "no",
    )

    # 步骤 2：缓存命中判断（把音质写进文件名，换档位后可各自缓存）
    existing = _find_cached_file(cache_dir, songmid, format_id, max_bytes)
    if existing is not None:
        register_temp_media_path(existing, ttl_seconds=cache_ttl_seconds)
        logger.info("[QQMusic] 缓存命中 -> %s", existing.name)
        return QQAudioResult(
            path=existing,
            songmid=songmid,
            format_id=format_id,
            title=title,
            duration=duration,
            filesize=existing.stat().st_size,
        )

    # 步骤 3：真正下载
    work_dir = cache_dir / "tmp" / f"qqmusic_{uuid.uuid4().hex}"
    work_dir.mkdir(parents=True, exist_ok=True)

    download_opts = _build_ydl_opts(
        format_selector=selector,
        max_bytes=max_bytes,
        socket_timeout=socket_timeout,
        retries=retries,
        cookiefile=cookie_arg,
        outtmpl=str(work_dir / "%(id)s.%(ext)s"),
    )

    try:
        _download_with_timeout(url, download_opts, download_timeout)

        candidate = _select_downloaded_file(work_dir)
        if candidate is None:
            raise QQMusicDownloadError("下载完成但没有找到音频文件。")

        filesize = candidate.stat().st_size
        if filesize > max_bytes:
            raise QQMusicSizeError(f"音频超过大小限制：{filesize / 1024 / 1024:.1f}MB。")

        suffix = candidate.suffix.lower() if candidate.suffix.lower() in AUDIO_SUFFIXES else ".mp3"
        final_path = cache_dir / f"qqmusic_{songmid}_{format_id}{suffix}"
        if final_path.exists():
            final_path.unlink()
        shutil.move(str(candidate), final_path)
    except QQMusicError:
        raise
    except Exception as exc:
        raise _map_extract_error(str(exc)) from exc
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    elapsed = time.time() - t_start
    logger.info(
        "[QQMusic] 下载完成 → songmid=%s, file=%s, size=%.1fMB, format=%s, elapsed=%.2fs",
        songmid,
        final_path.name,
        final_path.stat().st_size / 1024 / 1024,
        format_id,
        elapsed,
    )
    register_temp_media_path(final_path, ttl_seconds=cache_ttl_seconds)

    return QQAudioResult(
        path=final_path,
        songmid=songmid,
        format_id=format_id,
        title=title,
        duration=duration,
        filesize=final_path.stat().st_size,
    )


def _download_with_timeout(url: str, opts: dict[str, Any], timeout: int) -> None:
    """带超时的下载。

    yt-dlp 的 ``socket_timeout`` 只管单次 socket 读，管不住整体时长，
    这里额外用线程级超时兜住卡死的情况。
    """
    done: list[BaseException | None] = [None]

    def _run() -> None:
        try:
            with YoutubeDL(opts) as ydl:
                ydl.download([url])
        except BaseException as exc:  # noqa: BLE001 - 需要把异常带回主线程
            done[0] = exc

    import threading

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise QQMusicDownloadError("下载超时，请稍后再试。")
    if done[0] is not None:
        if isinstance(done[0], QQMusicError):
            raise done[0]
        raise _map_extract_error(str(done[0]))


def _build_ydl_opts(
    *,
    format_selector: str,
    max_bytes: int,
    socket_timeout: int,
    retries: int,
    cookiefile: str = "",
    outtmpl: str | None = None,
) -> dict[str, Any]:
    """构建 yt-dlp 选项。"""
    opts: dict[str, Any] = {
        "format": format_selector,
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
    if cookiefile:
        opts["cookiefile"] = cookiefile
    if outtmpl:
        opts["outtmpl"] = outtmpl
    return opts


def _find_cached_file(cache_dir: Path, songmid: str, format_id: str, max_bytes: int) -> Path | None:
    """在缓存目录中查找已下载的同档位文件。"""
    for path in cache_dir.glob(f"qqmusic_{songmid}_{format_id}.*"):
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


def _map_extract_error(message: str) -> QQMusicError:
    """把 yt-dlp 的报错映射成插件自己的异常类型。

    「没有可用格式」单独成一类：调用方需要结合接口的 ``pay_play`` 与是否配置了
    cookie，才能判断到底是 VIP 付费、缺 cookie 还是资源不可用。
    """
    lower = (message or "").lower()
    if any(hint in lower for hint in _NO_FORMAT_HINTS):
        return QQMusicNoFormatError("该曲目没有可用音质。")
    if "max-filesize" in lower or "larger than max" in lower:
        return QQMusicSizeError("音频超过大小限制。")
    if "timed out" in lower or "timeout" in lower:
        return QQMusicDownloadError("下载超时，请稍后再试。")
    if "unsupported url" in lower:
        return QQMusicDownloadError("这个链接不受支持，请换一个分享链接。")
    text = (message or "").strip()
    if not text:
        return QQMusicDownloadError("下载失败。")
    return QQMusicDownloadError(text[:180])
