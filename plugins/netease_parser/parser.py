"""
网易云音乐解析模块。

负责：
1. 从文本中提取 music.163.com 歌曲 URL
2. 调用 api-enhanced 服务器获取歌曲信息和 MP3 下载链接
"""

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger("HikariBot.NeteaseParser")

# =========================
# URL 正则
# =========================

# 匹配 music.163.com 的歌曲链接
# 支持的格式：
#   https://music.163.com/song/33894312
#   https://music.163.com/#/song?id=33894312
#   http://music.163.com/song/33894312/?xxx
#   music.163.com/#/song?id=33894312
NETEASE_SONG_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?music\.163\.com"
    r"(?:/#)?/song"
    r"(?:/(?P<id_path>\d{5,12})(?:/\?[^\s]*)?(?:\?[^\s]*)?"
    r"|\?id=(?P<id_query>\d{5,12}))",
    re.IGNORECASE,
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.7103.48 Safari/537.36"
)


# =========================
# 数据结构
# =========================


@dataclass
class NeteaseSongInfo:
    """歌曲基本信息。"""
    id: str
    name: str = ""
    artist: str = ""
    album: str = ""
    pic_url: str = ""


@dataclass
class NeteaseSongUrlResult:
    """歌曲音频 URL 查询结果。"""
    url: str = ""
    br: int = 0
    size: int = 0
    type: str = "mp3"
    code: int = 200


# =========================
# URL 提取
# =========================


def extract_song_ids(text: str) -> list[str]:
    """从文本中提取所有网易云音乐歌曲 ID（去重，保持顺序）。"""
    ids: list[str] = []
    seen: set[str] = set()

    for match in NETEASE_SONG_URL_RE.finditer(text):
        song_id = match.group("id_path") or match.group("id_query")
        if song_id and song_id not in seen:
            seen.add(song_id)
            ids.append(song_id)

    return ids


# =========================
# API 调用
# =========================


def _api_url(api_base: str, path: str) -> str:
    """构建完整的 API URL。"""
    base = api_base.rstrip("/")
    if not base.startswith("http"):
        base = f"http://{base}"
    return f"{base}{path}"


async def fetch_song_detail(
    song_id: str,
    api_base: str,
    timeout: int = 30,
    real_ip: str = "",
) -> NeteaseSongInfo:
    """
    获取歌曲详细信息。

    Args:
        song_id: 歌曲 ID
        api_base: API 服务器地址（如 http://127.0.0.1:3000）
        timeout: 请求超时（秒）
        real_ip: 用于绕过地区限制的国内 IP

    Returns:
        NeteaseSongInfo 对象

    Raises:
        httpx.HTTPError: API 请求失败
        ValueError: 响应格式异常
    """
    url = _api_url(api_base, f"/song/detail?ids={song_id}")
    if real_ip:
        url += f"&realIP={real_ip}"

    headers = {"User-Agent": USER_AGENT}

    logger.debug("[Netease] 请求歌曲详情 → song_id=%s", song_id)
    t_start = time.time()

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    elapsed = time.time() - t_start
    logger.debug("[Netease] 歌曲详情响应 → song_id=%s, elapsed=%.2fs", song_id, elapsed)

    if data.get("code") != 200:
        raise ValueError(f"API 返回异常 code={data.get('code')}: {data.get('msg', '')}")

    songs = data.get("songs", [])
    if not songs:
        raise ValueError("未找到歌曲信息")

    song = songs[0]
    artists = song.get("artists", [])
    artist_names = " / ".join(a.get("name", "") for a in artists if isinstance(a, dict))
    album_info = song.get("album", {})
    album_name = album_info.get("name", "") if isinstance(album_info, dict) else ""

    return NeteaseSongInfo(
        id=str(song.get("id", song_id)),
        name=str(song.get("name", "")),
        artist=artist_names,
        album=album_name,
        pic_url=album_info.get("picUrl", "") if isinstance(album_info, dict) else "",
    )


async def fetch_song_url(
    song_id: str,
    api_base: str,
    timeout: int = 30,
    real_ip: str = "",
) -> NeteaseSongUrlResult:
    """
    获取歌曲音频下载 URL。

    Args:
        song_id: 歌曲 ID
        api_base: API 服务器地址
        timeout: 请求超时（秒）
        real_ip: 用于绕过地区限制的国内 IP

    Returns:
        NeteaseSongUrlResult 对象，url 为空表示歌曲不可用

    Raises:
        httpx.HTTPError: API 请求失败
        ValueError: 响应格式异常
    """
    url = _api_url(api_base, f"/song/url?id={song_id}")
    if real_ip:
        url += f"&realIP={real_ip}"

    headers = {"User-Agent": USER_AGENT}

    logger.debug("[Netease] 请求歌曲 URL → song_id=%s", song_id)
    t_start = time.time()

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    elapsed = time.time() - t_start
    logger.debug("[Netease] 歌曲 URL 响应 → song_id=%s, elapsed=%.2fs", song_id, elapsed)

    if data.get("code") != 200:
        raise ValueError(f"API 返回异常 code={data.get('code')}: {data.get('msg', '')}")

    items = data.get("data", [])
    if not items:
        return NeteaseSongUrlResult(code=404)

    item = items[0]
    return NeteaseSongUrlResult(
        url=str(item.get("url") or ""),
        br=int(item.get("br", 0)),
        size=int(item.get("size", 0)),
        type=str(item.get("type", "mp3")),
        code=int(item.get("code", 200)),
    )
