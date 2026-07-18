"""
Instagram 媒体直链下载器。

替代 Cobalt API 处理 Instagram 链接。
工作流程：
1. 从 Instagram 页面提取 og:image / og:video 直链
2. 从直链下载媒体到服务器缓存
3. 返回本地文件路径供发送到 QQ

适用于：
- 单张图片帖子 → og:image
- 单个视频帖子 → og:video
- 轮播帖子 → 仅获取第一张图片（og:image）
"""

import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

from core.temp_media_cleaner import DEFAULT_TEMP_MEDIA_TTL_SECONDS, register_temp_media_path

logger = logging.getLogger("HikariBot.InsDownloader")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.7103.48 Safari/537.36"
)

TIMEOUT = httpx.Timeout(30.0, connect=15.0)


def extract_shortcode(url: str) -> Optional[str]:
    """从 Instagram URL 提取短代码 (post/reel/reels 的 ID)。"""
    parsed = urlparse(url)
    match = re.search(r"/(?:p|reel|reels|tv|stories)/[\w\-]+", parsed.path)
    if not match:
        return None
    return match.group(0).rsplit("/", 1)[-1]


async def fetch_direct_url(shortcode: str, max_retries: int = 3) -> Optional[dict]:
    """从 Instagram 页面提取媒体直链。

    页面结构不稳定（A/B 测试），最多重试 max_retries 次。

    Returns:
        {"url": str (直链), "type": "photo"|"video"} 或 None
    """
    page_url = f"https://www.instagram.com/p/{shortcode}/"

    # 多个 UA 轮流尝试，绕过 Instagram A/B 测试
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.7103.48 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.6533.103 Mobile Safari/537.36",
    ]

    for attempt in range(max_retries):
        headers = {
            "User-Agent": user_agents[attempt % len(user_agents)],
        }

        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(page_url, headers=headers)
            html = resp.text

        # 优先 og:video（视频帖子）
        m = re.search(r'<meta property="og:video" content="([^"]+)"', html)
        if m:
            video_url = m.group(1)
            if any(ext in video_url for ext in [".mp4", ".webm"]):
                logger.info(f"[InsDownloader] 视频直链 → attempt={attempt+1}")
                return {"url": video_url, "type": "video"}

        # og:image（图片帖子 / 视频封面兜底）
        m = re.search(r'<meta property="og:image" content="([^"]+)"', html)
        if m:
            image_url = m.group(1)
            logger.info(f"[InsDownloader] 图片直链 → attempt={attempt+1}")
            return {"url": image_url, "type": "photo"}

        logger.debug(f"[InsDownloader] 第 {attempt+1} 次尝试未找到 og 标签，重试...")

    # 兜底：用 oembed thumbnail_url
    try:
        oembed_url = f"https://i.instagram.com/api/v1/oembed/?url={page_url}"
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(oembed_url, headers={"User-Agent": user_agents[0]})
            data = r.json()
            thumb = data.get("thumbnail_url")
            if thumb:
                logger.info(f"[InsDownloader] oembed 兜底 → {thumb[:80]}...")
                return {"url": thumb, "type": "photo"}
    except Exception as e:
        logger.debug(f"[InsDownloader] oembed 兜底也失败: {e}")

    logger.warning(f"[InsDownloader] 无法提取直链 → {shortcode}")
    return None


def _cache_path(direct_url: str, cache_dir: str) -> Path:
    """根据直链 URL 生成缓存文件路径。"""
    ext = _guess_extension(direct_url)
    digest = hashlib.sha256(direct_url.encode("utf-8")).hexdigest()
    return Path(cache_dir) / f"ins_{digest[:16]}{ext}"


def _guess_extension(url: str) -> str:
    """从 URL 猜测文件扩展名。"""
    path = urlparse(url).path.lower()
    if ".mp4" in path or "/video/" in url:
        return ".mp4"
    if ".webm" in path:
        return ".webm"
    if ".png" in path:
        return ".png"
    if ".webp" in path:
        return ".jpg"
    return ".jpg"


async def download_media(
    shortcode: str,
    cache_dir: str = "/tmp/hikari_bot",
    cache_ttl_seconds: int = DEFAULT_TEMP_MEDIA_TTL_SECONDS,
) -> Optional[Path]:
    """提取直链 → 下载到服务器 → 返回本地路径。

    Args:
        shortcode: 帖子短代码（如 "DaVEzCGvmi6"）
        cache_dir: 缓存目录

    Returns:
        本地文件路径，失败返回 None
    """
    # 第一步：提取直链
    media = await fetch_direct_url(shortcode)
    if not media:
        return None

    direct_url = media["url"]

    # 第二步：下载到服务器
    path = _cache_path(direct_url, cache_dir)

    if path.exists() and path.stat().st_size > 0:
        register_temp_media_path(path, ttl_seconds=cache_ttl_seconds)
        logger.debug(f"[InsDownloader] 缓存命中 → {path.name}")
        return path

    logger.info(f"[InsDownloader] 从直链下载 → {direct_url[:80]}...")
    t_start = time.time()
    download_headers = {"User-Agent": USER_AGENT}

    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".part")
        try:
            async with client.stream("GET", direct_url, headers=download_headers) as resp:
                resp.raise_for_status()
                with tmp_path.open("wb") as f:
                    async for chunk in resp.aiter_bytes():
                        if chunk:
                            f.write(chunk)
            tmp_path.replace(path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    elapsed = time.time() - t_start
    size_kb = path.stat().st_size / 1024
    size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"
    logger.info(f"[InsDownloader] 下载完成 → {path.name} ({size_str}, {elapsed:.2f}s)")
    register_temp_media_path(path, ttl_seconds=cache_ttl_seconds)

    return path
