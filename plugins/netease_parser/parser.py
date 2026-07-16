"""
网易云音乐解析模块。

负责：
1. 从消息中提取 music.163.com 歌曲链接（含 QQ 卡片和短链接）
2. 解析 163cn.tv 短链接为真实歌曲 ID
3. 调用 api-enhanced 服务器获取歌曲信息和 MP3 下载链接
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
from nonebot.adapters.onebot.v11 import MessageEvent

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

# 匹配 163cn.tv 短链接（QQ 卡片分享常用）
NETEASE_SHORT_URL_RE = re.compile(
    r"(?:https?://)?163cn\.tv/[A-Za-z0-9]+",
    re.IGNORECASE,
)

# 通用 URL 提取（用于从卡片数据中匹配任意 http 链接）
GENERIC_URL_RE = re.compile(r"https?://[^\s\"'>]+", re.IGNORECASE)

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
# 从消息事件中提取 URL
# =========================


def _extract_card_urls(event: MessageEvent) -> list[str]:
    """
    从 QQ 卡片消息的元数据中提取 URL。

    QQ 音乐分享卡片会在 meta.detail_1.qqdocurl 中嵌入目标 URL。
    """
    urls: list[str] = []
    seen: set[str] = set()

    for segment in event.message:
        data = getattr(segment, "data", None)
        if data is None:
            continue
        card_urls = _card_url_candidates(data)
        for url in card_urls:
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def _card_url_candidates(data: Any) -> list[str]:
    """从单个消息段的 data 字段中提取可能的 URL。"""
    candidates: list[str] = []
    try:
        # 直接从 data 的 meta 字段提取（QQ 标准卡片格式）
        if isinstance(data, dict):
            curl_link = _extract_qqdocurl(data)
            if curl_link:
                candidates.append(curl_link)
            # 递归检查 data 中各字段
            for value in data.values():
                if isinstance(value, (dict, str)):
                    curl_link = _extract_qqdocurl(value)
                    if curl_link:
                        candidates.append(curl_link)
        # data 本身是 JSON 字符串的情况
        if isinstance(data, str) and data.startswith("{"):
            parsed = json.loads(data)
            curl_link = _extract_qqdocurl(parsed)
            if curl_link:
                candidates.append(curl_link)
    except (AttributeError, KeyError, json.JSONDecodeError, TypeError):
        pass
    return candidates


def _extract_qqdocurl(data: Any) -> Optional[str]:
    """从可能包含 QQ 卡片元数据的 dict 中提取 qqdocurl。"""
    if not isinstance(data, dict):
        return None
    meta = data.get("meta") or {}
    if isinstance(meta, dict):
        detail_1 = meta.get("detail_1") or {}
        if isinstance(detail_1, dict):
            url = detail_1.get("qqdocurl")
            if url and isinstance(url, str):
                return url
        news = meta.get("news") or {}
        if isinstance(news, dict):
            url = news.get("jumpUrl")
            if url and isinstance(url, str):
                return url
    return None


def extract_all_urls(event: MessageEvent) -> list[str]:
    """
    从消息事件中提取所有可能的 URL，包括：
    1. 消息正文中的文本 URL
    2. QQ 卡片元数据中的 URL

    返回去重、保持顺序的 URL 列表。
    """
    urls: list[str] = []
    seen: set[str] = set()

    # 从正文提取
    text = str(event.get_message())
    for match in GENERIC_URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;:!?)")
        if url not in seen:
            seen.add(url)
            urls.append(url)

    # 从卡片元数据提取
    for url in _extract_card_urls(event):
        if url not in seen:
            seen.add(url)
            urls.append(url)

    return urls


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


def has_netease_url(text: str) -> bool:
    """检查文本中是否包含网易云音乐相关链接。"""
    if NETEASE_SONG_URL_RE.search(text):
        return True
    if NETEASE_SHORT_URL_RE.search(text):
        return True
    return False


def has_short_url(text: str) -> bool:
    """检查文本中是否包含 163cn.tv 短链接。"""
    return bool(NETEASE_SHORT_URL_RE.search(text))


async def resolve_short_url(short_url: str, timeout: int = 10) -> Optional[str]:
    """
    解析 163cn.tv 短链接，跟随重定向获取真实 URL。

    Args:
        short_url: 短链接 URL
        timeout: 请求超时（秒）

    Returns:
        重定向后的真实 URL，解析失败返回 None
    """
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=5.0),
            follow_redirects=True,
            max_redirects=5,
        ) as client:
            resp = await client.get(short_url, headers={"User-Agent": USER_AGENT})
            # 获取最终 URL（重定向链的终点）
            final_url = str(resp.url)
            if final_url and final_url != short_url:
                logger.debug("[Netease] 短链接解析 → %s → %s", short_url, final_url)
                return final_url
            return None
    except httpx.HTTPError as e:
        logger.warning("[Netease] 短链接解析失败 → %s: %s", short_url, e)
        return None


def extract_song_id_from_url(url: str) -> Optional[str]:
    """
    从 URL 中提取歌曲 ID。

    支持格式：
    - https://music.163.com/song/33894312
    - https://music.163.com/#/song?id=33894312
    """
    match = NETEASE_SONG_URL_RE.search(url)
    if match:
        return match.group("id_path") or match.group("id_query")
    return None


async def extract_song_ids_from_event(event: MessageEvent) -> list[str]:
    """
    从消息事件中提取所有网易云音乐歌曲 ID。

    处理流程：
    1. 从消息正文和卡片元数据中提取所有 URL
    2. 直接匹配 music.163.com/song/... 格式 → 提取 ID
    3. 匹配 163cn.tv 短链接 → 跟随重定向 → 从目标 URL 提取 ID
    4. 去重返回

    Returns:
        去重、保持顺序的歌曲 ID 列表
    """
    ids: list[str] = []
    seen_ids: set[str] = set()
    resolved_short_urls: set[str] = set()
    short_urls_to_resolve: list[str] = []

    all_urls = extract_all_urls(event)

    for url in all_urls:
        # 尝试直接匹配 music.163.com/song/...
        song_id = extract_song_id_from_url(url)
        if song_id and song_id not in seen_ids:
            seen_ids.add(song_id)
            ids.append(song_id)
            continue

        # 匹配 163cn.tv 短链接，后续批量解析
        if NETEASE_SHORT_URL_RE.match(url) and url not in resolved_short_urls:
            resolved_short_urls.add(url)
            short_urls_to_resolve.append(url)

    # 批量解析短链接
    for short_url in short_urls_to_resolve:
        resolved = await resolve_short_url(short_url)
        if resolved:
            song_id = extract_song_id_from_url(resolved)
            if song_id and song_id not in seen_ids:
                seen_ids.add(song_id)
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
