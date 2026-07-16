"""
网易云音乐音频下载模块。

负责：
1. 从 api-enhanced 返回的 MP3 URL 下载到本地缓存
2. SHA256 哈希去重缓存
3. 流式下载并实时检查大小限制
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
CHUNK_LOG_INTERVAL_BYTES = 10 * 1024 * 1024  # 每 10MB 打印一次进度


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

    Raises:
        RuntimeError: 下载失败或超过大小限制
        httpx.TimeoutException: 下载超时
    """
    path = _cache_path(url, cache_dir)
    max_bytes = max(int(max_file_mb), 1) * 1024 * 1024

    # ===== 缓存命中 =====
    if path.exists() and path.stat().st_size > 0:
        file_size = path.stat().st_size
        if file_size > max_bytes:
            logger.warning(
                "[Netease] 缓存文件超过大小限制 → %s (%.1fMB > %dMB)",
                path.name, file_size / 1024 / 1024, max_file_mb,
            )
            raise RuntimeError(
                f"缓存音频超过大小限制：{file_size / 1024 / 1024:.1f}MB"
            )
        register_temp_media_path(path, ttl_seconds=cache_ttl_seconds)
        logger.info(
            "[Netease] 缓存命中 → %s (%.1fMB, 已延期 TTL=%ds)",
            path.name, file_size / 1024 / 1024, cache_ttl_seconds,
        )
        return path

    # ===== 下载 =====
    t_start = time.time()
    logger.info("[Netease] 开始下载 → %s", url[:120])

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

                # 检查 Content-Length
                cl = resp.headers.get("content-length")
                if cl and cl.isdigit():
                    remote_size = int(cl)
                    logger.info(
                        "[Netease] 下载: Content-Length=%.1fMB, 限制=%dMB",
                        remote_size / 1024 / 1024, max_file_mb,
                    )
                    if remote_size > max_bytes:
                        raise RuntimeError(
                            f"音频超过大小限制：{remote_size / 1024 / 1024:.1f}MB"
                        )
                else:
                    remote_size = "未知"
                    logger.info(
                        "[Netease] 下载: Content-Length=%s, 限制=%dMB",
                        remote_size, max_file_mb,
                    )

                # 流式写入
                written = 0
                last_chunk_log = 0
                with tmp_path.open("wb") as f:
                    async for chunk in resp.aiter_bytes():
                        if chunk:
                            written += len(chunk)
                            if written > max_bytes:
                                raise RuntimeError(
                                    f"音频超过大小限制：{written / 1024 / 1024:.1f}MB"
                                )
                            f.write(chunk)

                            # 每 10MB 打印一次进度
                            if written - last_chunk_log >= CHUNK_LOG_INTERVAL_BYTES:
                                last_chunk_log = written
                                elapsed_now = time.time() - t_start
                                speed = written / 1024 / 1024 / elapsed_now if elapsed_now > 0 else 0
                                logger.info(
                                    "[Netease] 下载中... %.1fMB / %s (%.1fMB/s, %.1fs)",
                                    written / 1024 / 1024,
                                    f"{remote_size / 1024 / 1024:.1f}MB" if isinstance(remote_size, int) else "未知",
                                    speed, elapsed_now,
                                )

                tmp_path.replace(path)

        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    elapsed = time.time() - t_start
    file_size = path.stat().st_size
    speed = file_size / 1024 / 1024 / elapsed if elapsed > 0 else 0

    logger.info(
        "[Netease] 下载完成 → %s (%.1fMB, %.1fMB/s, %.1fs)",
        path.name, file_size / 1024 / 1024, speed, elapsed,
    )

    register_temp_media_path(path, ttl_seconds=cache_ttl_seconds)
    logger.debug("[Netease] 已注册缓存 TTL 清理 → %s (TTL=%ds)", path.name, cache_ttl_seconds)

    return path
