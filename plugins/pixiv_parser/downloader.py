"""
Pixiv 图片下载模块。

负责：
1. 下载 Pixiv 图片到本地缓存
2. 优先下载 original，超限则降级到 regular
3. 缓存命中时跳过下载
"""

import hashlib
import logging
import time
from pathlib import Path
from typing import Optional

import httpx

from .parser import _get_http_client

logger = logging.getLogger("HikariBot.PixivDownloader")


def get_suffix_from_url(url: str) -> str:
    """从 URL 推断文件后缀。"""
    path = url.split("?", 1)[0]
    suffix = Path(path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        return suffix
    return ".jpg"


def cache_path_for_url(url: str, cache_dir: str) -> Path:
    """根据 URL 生成缓存文件路径（SHA256 哈希）。"""
    suffix = get_suffix_from_url(url)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return Path(cache_dir) / f"{digest}{suffix}"


def file_as_uri(path: Path) -> str:
    """将本地路径转为 file:// URI。"""
    return path.resolve().as_uri()


async def download_image(
    url: str,
    illust_id: str,
    cookie: str,
    proxy: str = "",
    cache_dir: str = "/tmp/hikari_bot",
) -> Path:
    """
    下载单张图片到本地缓存。

    实现缓存机制：如果文件已存在且非空，直接返回路径。

    Returns:
        本地文件路径
    """
    path = cache_path_for_url(url, cache_dir)

    # 缓存命中
    if path.exists() and path.stat().st_size > 0:
        size_kb = path.stat().st_size / 1024
        logger.debug(f"[Pixiv] 缓存命中 pid={illust_id} → {path.name} ({size_kb:.1f} KB)")
        return path

    # 下载
    logger.info(f"[Pixiv] 下载图片 pid={illust_id} → {url[:100]}...")
    t_start = time.time()

    async with _get_http_client(illust_id, cookie, proxy) as client:
        resp = await client.get(url)
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        if not content_type.startswith("image/"):
            logger.error(
                f"[Pixiv] 非图片响应 pid={illust_id} → "
                f"content-type={content_type}, url={url[:100]}..."
            )
            raise RuntimeError(f"下载到的不是图片：{content_type}")

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)

    elapsed = time.time() - t_start
    file_size_kb = path.stat().st_size / 1024
    logger.info(
        f"[Pixiv] 下载完成 pid={illust_id} → {path.name} "
        f"({file_size_kb:.1f} KB, {elapsed:.2f}s)"
    )

    return path


async def download_with_fallback(
    page,
    illust_id: str,
    cookie: str,
    proxy: str = "",
    cache_dir: str = "/tmp/hikari_bot",
    max_file_mb: int = 25,
) -> tuple[Path, bool]:
    """
    下载图片，优先 original，超限则降级到 regular。

    Returns:
        (文件路径, 是否为原图)
    """
    from .parser import PixivPage

    max_bytes = max(max_file_mb, 1) * 1024 * 1024
    logger.debug(
        f"[Pixiv] 下载页面 pid={illust_id} p={page.index} → "
        f"尺寸={page.width}x{page.height}, 大小限制={max_file_mb}MB"
    )

    # 尝试 original
    original_path = await download_image(page.original_url, illust_id, cookie, proxy, cache_dir)
    original_size_mb = original_path.stat().st_size / 1024 / 1024

    if original_path.stat().st_size <= max_bytes:
        logger.debug(f"[Pixiv] 使用原图 pid={illust_id} p={page.index} → {original_size_mb:.2f}MB")
        return original_path, True

    # original 过大，尝试 regular
    logger.warning(
        f"[Pixiv] 原图过大，尝试 regular → pid={illust_id} p={page.index} "
        f"original={original_size_mb:.2f}MB > {max_file_mb}MB"
    )

    regular_path = await download_image(page.regular_url, illust_id, cookie, proxy, cache_dir)
    regular_size_mb = regular_path.stat().st_size / 1024 / 1024

    if regular_path.stat().st_size <= max_bytes:
        logger.info(
            f"[Pixiv] 降级为 regular 图 pid={illust_id} p={page.index} → "
            f"regular={regular_size_mb:.2f}MB (原图 {original_size_mb:.2f}MB)"
        )
        return regular_path, False

    # 两者都过大
    logger.error(
        f"[Pixiv] 图片过大 → pid={illust_id} p={page.index} "
        f"original={original_size_mb:.2f}MB, regular={regular_size_mb:.2f}MB, "
        f"limit={max_file_mb}MB"
    )
    raise RuntimeError(
        f"图片过大，original={original_size_mb:.1f}MB，regular={regular_size_mb:.1f}MB"
    )
