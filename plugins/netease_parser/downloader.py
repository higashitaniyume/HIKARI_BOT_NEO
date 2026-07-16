"""
网易云音乐音频下载模块。

负责：
1. 从 api-enhanced 返回的 MP3 URL 下载到本地缓存
2. SHA256 哈希去重缓存
3. 大小限制检查
"""

import hashlib
import logging
import time
from pathlib import Path

import httpx

from core.temp_media_cleaner import DEFAULT_TEMP_MEDIA_TTL_SECONDS, register_temp_media_path

logger = logging.getLogger("HikariBot.NeteaseDownloader")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.7103.48 Safari/537.36"
)


def _cache_path(url: str, cache_dir: str) -> Path:
    """根据 URL 生成缓存文件路径。"""
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return Path(cache_dir) / f"netease_{digest[:16]}.mp3"


def file_as_uri(path: Path) -> str:
    """将本地路径转为 file:// URI。"""
    return path.resolve().as_uri()


async def download_audio(
    url: str,
    cache_dir: str = "/tmp/hikari_bot/netease",
    timeout: int = 30,
    max_file_mb: int = 50,
    cache_ttl_seconds: int = DEFAULT_TEMP_MEDIA_TTL_SECONDS,
) -> Path:
    """
    下载音频文件到本地缓存。

    Args:
        url: MP3 下载 URL
        cache_dir: 缓存目录
        timeout: 请求超时（秒）
        max_file_mb: 最大文件大小（MB）
        cache_ttl_seconds: 缓存 TTL（秒）

    Returns:
        本地文件路径

    Raises:
        RuntimeError: 下载失败或超过大小限制
    """
    path = _cache_path(url, cache_dir)
    max_bytes = max(int(max_file_mb), 1) * 1024 * 1024

    # 缓存命中
    if path.exists() and path.stat().st_size > 0:
        if path.stat().st_size > max_bytes:
            raise RuntimeError(
                f"缓存音频超过大小限制：{path.stat().st_size / 1024 / 1024:.1f}MB"
            )
        register_temp_media_path(path, ttl_seconds=cache_ttl_seconds)
        size_kb = path.stat().st_size / 1024
        logger.debug("[Netease] 缓存命中 → %s (%.1f KB)", path.name, size_kb)
        return path

    # 下载：流式写入
    logger.info("[Netease] 下载音频 → %s...", url[:100])
    t_start = time.time()

    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=15.0),
        follow_redirects=True,
    ) as client:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".part")
        try:
            async with client.stream("GET", url, headers=headers) as resp:
                resp.raise_for_status()
                content_length = resp.headers.get("content-length")
                content_length_bytes = (
                    int(content_length) if content_length and content_length.isdigit() else 0
                )
                if content_length_bytes > max_bytes:
                    raise RuntimeError(
                        f"音频超过大小限制：{content_length_bytes / 1024 / 1024:.1f}MB"
                    )
                written = 0
                with tmp_path.open("wb") as f:
                    async for chunk in resp.aiter_bytes():
                        if chunk:
                            written += len(chunk)
                            if written > max_bytes:
                                raise RuntimeError(
                                    f"音频超过大小限制：{written / 1024 / 1024:.1f}MB"
                                )
                            f.write(chunk)
            tmp_path.replace(path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    elapsed = time.time() - t_start
    size_kb = path.stat().st_size / 1024
    size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"
    logger.info("[Netease] 下载完成 → %s (%s, %.2fs)", path.name, size_str, elapsed)
    register_temp_media_path(path, ttl_seconds=cache_ttl_seconds)

    return path
