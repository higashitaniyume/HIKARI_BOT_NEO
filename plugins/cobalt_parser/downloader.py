"""
Cobalt 媒体下载模块。

负责：
1. 从 cobalt 返回的 URL 下载媒体到本地
2. 缓存机制（SHA256 哈希去重）
3. 文件名冲突避免
"""

import hashlib
import logging
import time
from pathlib import Path

import httpx

logger = logging.getLogger("HikariBot.CobaltDownloader")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.7103.48 Safari/537.36"
)


def get_suffix(filename: str) -> str:
    """从文件名提取后缀。"""
    suffix = Path(filename).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".webm", ".mov", ".mkv"}:
        return suffix
    return ".mp4"


def _cache_path(url: str, cache_dir: str) -> Path:
    """根据 URL 生成缓存文件路径。"""
    suffix = get_suffix(url)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return Path(cache_dir) / f"cobalt_{digest[:16]}{suffix}"


def file_as_uri(path: Path) -> str:
    """将本地路径转为 file:// URI。"""
    return path.resolve().as_uri()


async def download_media(
    url: str,
    filename: str,
    cache_dir: str = "/tmp/hikari_bot",
    timeout: int = 90,
    max_file_mb: int = 200,
) -> Path:
    """
    下载媒体文件到本地缓存。

    Args:
        url: 下载 URL（cobalt tunnel URL 或直链）
        filename: 原始文件名（用于后缀推断）
        cache_dir: 缓存目录

    Returns:
        本地文件路径
    """
    path = _cache_path(url, cache_dir)
    max_bytes = max(int(max_file_mb), 1) * 1024 * 1024

    # 缓存命中
    if path.exists() and path.stat().st_size > 0:
        if path.stat().st_size > max_bytes:
            raise RuntimeError(f"缓存媒体超过大小限制：{path.stat().st_size / 1024 / 1024:.1f}MB")
        size_kb = path.stat().st_size / 1024
        logger.debug(f"[Cobalt] 缓存命中 → {path.name} ({size_kb:.1f} KB)")
        return path

    # 下载：流式写入，避免大视频一次性进入内存；不限制文件大小。
    logger.info(f"[Cobalt] 下载媒体 → {url[:100]}...")
    t_start = time.time()

    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=20.0),
        follow_redirects=True,
    ) as client:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".part")
        try:
            async with client.stream("GET", url, headers=headers) as resp:
                resp.raise_for_status()
                content_length = resp.headers.get("content-length")
                content_length_bytes = int(content_length) if content_length and content_length.isdigit() else 0
                if content_length_bytes > max_bytes:
                    raise RuntimeError(
                        f"媒体超过大小限制：{content_length_bytes / 1024 / 1024:.1f}MB"
                    )
                written = 0
                with tmp_path.open("wb") as f:
                    async for chunk in resp.aiter_bytes():
                        if chunk:
                            written += len(chunk)
                            if written > max_bytes:
                                raise RuntimeError(
                                    f"媒体超过大小限制：{written / 1024 / 1024:.1f}MB"
                                )
                            f.write(chunk)
            tmp_path.replace(path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    elapsed = time.time() - t_start
    size_kb = path.stat().st_size / 1024
    size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"
    logger.info(f"[Cobalt] 下载完成 → {path.name} ({size_str}, {elapsed:.2f}s)")

    return path
