from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from core.bot_identity import bot_user_agent


class OsuDownloadError(RuntimeError):
    pass


class OsuDownloadNeedsLogin(OsuDownloadError):
    pass


@dataclass(slots=True)
class DownloadedBeatmapset:
    beatmapset_id: int
    path: Path
    official_url: str


def official_download_url(beatmapset_id: int, *, no_video: bool = True) -> str:
    url = f"https://osu.ppy.sh/beatmapsets/{int(beatmapset_id)}/download"
    if no_video:
        url += "?noVideo=1"
    return url


def official_page_url(beatmapset_id: int) -> str:
    return f"https://osu.ppy.sh/beatmapsets/{int(beatmapset_id)}"


def extract_beatmapset_id(text: str) -> int | None:
    raw = text.strip()
    if raw.isdigit():
        return int(raw)
    match = re.search(r"osu\.ppy\.sh/beatmapsets/(\d+)", raw)
    if match:
        return int(match.group(1))
    return None


def cache_path_for_beatmapset(beatmapset_id: int, cache_dir: Path) -> Path:
    digest = hashlib.sha256(str(beatmapset_id).encode("utf-8")).hexdigest()[:16]
    return cache_dir / "downloads" / f"osu_{beatmapset_id}_{digest}.osz"


def _headers(session_cookie: str = "") -> dict[str, str]:
    headers = {
        "Accept": "application/octet-stream,text/html;q=0.9,*/*;q=0.8",
        "User-Agent": bot_user_agent("osu_info"),
        "Referer": "https://osu.ppy.sh/beatmapsets",
    }
    if session_cookie.strip():
        headers["Cookie"] = session_cookie.strip()
    return headers


def _looks_like_osz(response: httpx.Response) -> bool:
    content_type = response.headers.get("content-type", "").casefold()
    disposition = response.headers.get("content-disposition", "").casefold()
    return (
        ".osz" in disposition
        or "filename" in disposition
        or content_type.startswith("application/octet-stream")
        or "application/zip" in content_type
        or "application/x-osu" in content_type
    )


async def download_beatmapset_from_official(
    beatmapset_id: int,
    *,
    cache_dir: Path,
    no_video: bool = True,
    max_file_mb: int = 80,
    session_cookie: str = "",
    proxy: str = "",
    timeout: float = 60,
) -> DownloadedBeatmapset:
    cache_dir = Path(cache_dir)
    path = cache_path_for_beatmapset(beatmapset_id, cache_dir)
    if path.exists() and path.stat().st_size > 0:
        return DownloadedBeatmapset(
            beatmapset_id=beatmapset_id,
            path=path,
            official_url=official_download_url(beatmapset_id, no_video=no_video),
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".osz.part")
    tmp_path.unlink(missing_ok=True)
    url = official_download_url(beatmapset_id, no_video=no_video)
    max_bytes = int(max_file_mb) * 1024 * 1024
    kwargs: dict[str, Any] = {
        "timeout": timeout,
        "follow_redirects": True,
        "headers": _headers(session_cookie),
    }
    if proxy:
        kwargs["proxy"] = proxy

    completed = False
    try:
        async with httpx.AsyncClient(**kwargs) as client:
            async with client.stream("GET", url) as response:
                if response.status_code in {401, 403}:
                    raise OsuDownloadNeedsLogin(f"官方源需要登录: HTTP {response.status_code}")
                response.raise_for_status()
                final_url = str(response.url)
                if final_url.rstrip("/").endswith(f"/beatmapsets/{beatmapset_id}") or not _looks_like_osz(response):
                    raise OsuDownloadNeedsLogin("官方源返回了谱面页面而不是 .osz 文件")

                written = 0
                with tmp_path.open("wb") as f:
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                        written += len(chunk)
                        if written > max_bytes:
                            raise OsuDownloadError(f"谱面文件超过大小限制: {max_file_mb} MB")
                        f.write(chunk)
                completed = True
    except httpx.RequestError as e:
        raise OsuDownloadError(f"官方源连接失败: {type(e).__name__}") from e
    except httpx.HTTPStatusError as e:
        raise OsuDownloadError(f"官方源下载失败: HTTP {e.response.status_code}") from e
    finally:
        if tmp_path.exists() and not completed:
            # Keep failed partial files out of the shared temp directory.
            tmp_path.unlink(missing_ok=True)

    if not tmp_path.exists() or tmp_path.stat().st_size <= 0:
        raise OsuDownloadError("官方源没有返回可用的 .osz 文件")
    tmp_path.replace(path)
    return DownloadedBeatmapset(beatmapset_id=beatmapset_id, path=path, official_url=url)
