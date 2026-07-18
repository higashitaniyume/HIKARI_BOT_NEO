"""
Instagram yt-dlp 下载模块。

yt-dlp 的 Python API 是同步的；这里通过 asyncio.to_thread 包装，避免阻塞
NoneBot 事件循环。

配合 __init__.py 的三层回退流程使用：
  Cobalt → yt-dlp（本模块）→ og:image 直链提取
"""

import asyncio
import logging
import tempfile
import time
from pathlib import Path
from typing import Optional

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from core.temp_media_cleaner import DEFAULT_TEMP_MEDIA_TTL_SECONDS, register_temp_media_path

logger = logging.getLogger("HikariBot.InsYtdlp")


def _write_cookie_file(cookie_str: str) -> str:
    """把 Cookie 字符串写成 Netscape 格式临时文件，供 yt-dlp 使用。"""
    if not cookie_str:
        return ""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
    tmp.write("# Netscape HTTP Cookie File\n")
    tmp.write(".instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\t")
    # 尝试从 cookie 字符串提取 sessionid
    for part in cookie_str.split(";"):
        part = part.strip()
        if part.startswith("sessionid="):
            val = part.split("=", 1)[1]
            tmp.write(val + "\n")
            break
    for name in ("csrftoken", "ds_user_id", "mid", "ig_did"):
        for part in cookie_str.split(";"):
            part = part.strip()
            if part.startswith(f"{name}="):
                val = part.split("=", 1)[1]
                tmp.write(f".instagram.com\tTRUE\t/\tTRUE\t0\t{name}\t{val}\n")
                break
    tmp.close()
    logger.debug(f"[InsYtdlp] 临时 cookie 文件已写入: {tmp.name}")
    return tmp.name


def _build_ydl_opts(cookie_file: str, output_path: str, max_file_mb: int) -> dict:
    opts: dict = {
        "outtmpl": output_path,
        "format": "bestvideo+bestaudio/best",
        "max_filesize": max_file_mb * 1024 * 1024,
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "retries": 3,
        "fragment_retries": 3,
        "ignoreerrors": False,
    }
    if cookie_file:
        opts["cookiefile"] = cookie_file
    return opts


async def download_instagram_video(
    url: str,
    cookie_str: str,
    cache_dir: str = "/tmp/hikari_bot",
    max_file_mb: int = 200,
    cache_ttl_seconds: int = DEFAULT_TEMP_MEDIA_TTL_SECONDS,
) -> Optional[Path]:
    """用 yt-dlp 下载 Instagram 视频。

    只处理视频帖子（Reels/视频）；单图帖子 yt-dlp 会返回
    "No video formats found" 错误，调用方应回退到其他方式。

    Returns:
        下载后的文件路径，失败返回 None
    """
    # 写 cookie 临时文件
    cookie_file = _write_cookie_file(cookie_str) if cookie_str else ""

    # 生成输出路径
    shortcode = url.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
    output_path = str(Path(cache_dir) / f"ins_ytdlp_{shortcode[:16]}_%(id)s.%(ext)s")

    ydl_opts = _build_ydl_opts(cookie_file, output_path, max_file_mb)

    t_start = time.time()
    try:
        logger.info(f"[InsYtdlp] yt-dlp 开始下载 → {url[:60]}...")

        def _sync_download():
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                # yt-dlp 返回下载文件的路径
                fp = ydl.prepare_filename(info)
                # 实际扩展名可能和 prepare_filename 不同
                for suffix in (".mp4", ".mkv", ".webm", ".mov"):
                    p = Path(fp).with_suffix(suffix)
                    if p.exists():
                        return p
                    # 也可能是 info 里实际下载的格式
                # 兜底：搜索缓存目录下最新的 mp4
                cache = Path(cache_dir)
                candidates = sorted(cache.glob(f"ins_ytdlp_{shortcode[:16]}*"), key=lambda x: x.stat().st_mtime)
                if candidates:
                    return candidates[-1]
                return None

        file_path = await asyncio.to_thread(_sync_download)

        if file_path and file_path.exists():
            elapsed = time.time() - t_start
            size_mb = file_path.stat().st_size / 1024 / 1024
            logger.info(f"[InsYtdlp] 下载完成 → {file_path.name} ({size_mb:.1f}MB, {elapsed:.1f}s)")
            register_temp_media_path(file_path, ttl_seconds=cache_ttl_seconds)
            return file_path

        logger.warning(f"[InsYtdlp] 下载后未找到文件 → {shortcode}")
        return None

    except DownloadError as e:
        err_msg = str(e)
        if "No video formats found" in err_msg:
            logger.info(f"[InsYtdlp] 单图帖子，yt-dlp 不处理 → {shortcode}")
        else:
            logger.warning(f"[InsYtdlp] yt-dlp 下载失败: {e}")
        return None

    except Exception as e:
        logger.warning(f"[InsYtdlp] 未知错误: {e}")
        return None

    finally:
        # 清理临时 cookie 文件
        if cookie_file:
            Path(cookie_file).unlink(missing_ok=True)
