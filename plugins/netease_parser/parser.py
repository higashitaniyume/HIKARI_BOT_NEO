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
NETEASE_SONG_URL_RE = re.compile(
    r"(?:https?://)?"
    r"(?:(?:www|y)\.)?music\.163\.com"
    r"(?:/#)?"
    r"(?:/m)?/song"
    r"(?:/(?P<id_path>\d{5,12})(?:/\?[^\s]*)?(?:\?[^\s]*)?"
    r"|\?(?:[^\s]*?&)?id=(?P<id_query>\d{5,12}))",
    re.IGNORECASE,
)

# 匹配 163cn.tv 短链接（QQ 卡片分享常用）
NETEASE_SHORT_URL_RE = re.compile(
    r"(?:https?://)?163cn\.tv/[A-Za-z0-9]+",
    re.IGNORECASE,
)

# 匹配 music.163.com 的专辑链接
# 格式: https://music.163.com/album/387568337
#       https://y.music.163.com/m/album?id=387568337
NETEASE_ALBUM_URL_RE = re.compile(
    r"(?:https?://)?"
    r"(?:(?:www|y)\.)?music\.163\.com"
    r"(?:/#)?"
    r"(?:/m)?/album"
    r"(?:/(?P<id_path>\d{5,12})(?:/\?[^\s]*)?(?:\?[^\s]*)?"
    r"|\?(?:[^\s]*?&)?id=(?P<id_query>\d{5,12}))",
    re.IGNORECASE,
)

# 通用 URL 提取
GENERIC_URL_RE = re.compile(r"https?://[^\s\"'>]+", re.IGNORECASE)


def _unescape_cq(text: str) -> str:
    """还原 OneBot V11 CQ 码转义，方便正则匹配 URL。"""
    return text.replace("&#44;", ",").replace("&#91;", "[").replace("&#93;", "]").replace("&amp;", "&")

# 匹配网易云音乐播客/电台节目链接
# 格式：https://y.music.163.com/m/program?id=2538607775
NETEASE_PROGRAM_URL_RE = re.compile(
    r"(?:https?://)?(?:y\.)?music\.163\.com"
    r"(?:/m)?/program\?(?:[^\s]*?&)?id=(?P<id>\d{5,12})",
    re.IGNORECASE,
)

# 匹配 music.163.com 的歌单链接
# 格式: https://music.163.com/m/playlist?id=18147720055
#       https://music.163.com/playlist/18147720055/
NETEASE_PLAYLIST_URL_RE = re.compile(
    r"(?:https?://)?"
    r"(?:(?:www|y)\.)?music\.163\.com"
    r"(?:/#)?"
    r"(?:/m)?/playlist"
    r"(?:/(?P<id_path>\d{5,12})(?:/\?[^\s]*)?(?:\?[^\s]*)?"
    r"|\?(?:[^\s]*?&)?id=(?P<id_query>\d{5,12}))",
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


@dataclass
class ParsedLinks:
    """一次消息中提取出的网易云链接分类结果。"""

    song_ids: list[str] = field(default_factory=list)
    album_ids: list[str] = field(default_factory=list)
    playlist_ids: list[str] = field(default_factory=list)
    program_ids: list[str] = field(default_factory=list)

    def any(self) -> bool:
        return bool(self.song_ids or self.album_ids or self.playlist_ids or self.program_ids)


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

    if urls:
        logger.debug(
            "[Netease] 从卡片元数据提取到 %d 个 URL: %s",
            len(urls), [u[:60] for u in urls],
        )
    return urls


def _card_url_candidates(data: Any) -> list[str]:
    """从单个消息段的 data 字段中提取可能的 URL。

    QQ 卡片的 data 结构有多种形态：
    - data 直接是已解析的 dict（含 meta 字段）
    - data 是 {"data": "{...json...}", ...} 嵌套
    - data 本身是 JSON 字符串 "{...}"
    """
    candidates: list[str] = []
    try:
        # 先把可能嵌套的 JSON 字符串提取出来统一解析
        candidates = _extract_urls_from_data_value(data)

        # 如果 data 是 dict 且有 "data" 字段（嵌套 JSON 字符串），也解析它
        if isinstance(data, dict):
            inner = data.get("data")
            if isinstance(inner, str) and inner.startswith("{"):
                candidates.extend(_extract_urls_from_data_value(inner))
    except (AttributeError, KeyError, json.JSONDecodeError, TypeError) as e:
        logger.debug("[Netease] 卡片 URL 提取异常: %s", e)
    return candidates


def _extract_urls_from_data_value(value: Any) -> list[str]:
    """从单个 data 值中提取 URL。"""
    urls: list[str] = []
    if isinstance(value, str) and value.startswith("{"):
        parsed = json.loads(value)
        url = _extract_qqdocurl(parsed)
        if url:
            urls.append(url)
        # 递归检查 dict 的每个值
        if isinstance(parsed, dict):
            for v in parsed.values():
                if isinstance(v, str) and v.startswith("http"):
                    urls.append(v)
    elif isinstance(value, dict):
        url = _extract_qqdocurl(value)
        if url:
            urls.append(url)
        for v in value.values():
            if isinstance(v, dict):
                url = _extract_qqdocurl(v)
                if url:
                    urls.append(url)
    return urls


def _extract_qqdocurl(data: Any) -> Optional[str]:
    """从可能包含 QQ 卡片元数据的 dict 中提取 URL。

    QQ 卡片有不同的格式：
    - 一般分享: meta.detail_1.qqdocurl
    - 新闻分享: meta.news.jumpUrl
    - 音乐分享 (com.tencent.music.lua): meta.music.jumpUrl
    """
    if not isinstance(data, dict):
        return None
    meta = data.get("meta") or {}
    if isinstance(meta, dict):
        # 格式 1: meta.detail_1.qqdocurl
        detail_1 = meta.get("detail_1") or {}
        if isinstance(detail_1, dict):
            url = detail_1.get("qqdocurl")
            if url and isinstance(url, str):
                return url
        # 格式 2: meta.news.jumpUrl
        news = meta.get("news") or {}
        if isinstance(news, dict):
            url = news.get("jumpUrl")
            if url and isinstance(url, str):
                return url
        # 格式 3: meta.music.jumpUrl（QQ 音乐分享卡片）
        music = meta.get("music") or {}
        if isinstance(music, dict):
            url = music.get("jumpUrl")
            if url and isinstance(url, str):
                return url
    return None


def extract_all_urls(event: MessageEvent) -> list[str]:
    """
    从消息事件中提取所有可能的 URL。

    包括消息正文的文本 URL 和 QQ 卡片元数据中的 URL。
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

    if urls:
        logger.debug("[Netease] extract_all_urls 共提取到 %d 个 URL", len(urls))
    else:
        logger.debug("[Netease] extract_all_urls 未提取到任何 URL")
    return urls


def extract_song_ids(text: str) -> list[str]:
    """从文本中提取所有网易云音乐歌曲 ID（去重，保持顺序）。"""
    ids: list[str] = []
    seen: set[str] = set()
    clean = _unescape_cq(text)

    for match in NETEASE_SONG_URL_RE.finditer(clean):
        song_id = match.group("id_path") or match.group("id_query")
        if song_id and song_id not in seen:
            seen.add(song_id)
            ids.append(song_id)

    return ids


def has_netease_url(text: str) -> bool:
    """检查文本中是否包含网易云音乐相关链接。"""
    # 先还原 CQ 码转义（&amp; → &），否则正则匹配不到转义后的 & 符号
    clean = _unescape_cq(text)
    if NETEASE_SONG_URL_RE.search(clean):
        return True
    if NETEASE_SHORT_URL_RE.search(text):  # 短链接无 & 符号，不用反转义
        return True
    if NETEASE_PROGRAM_URL_RE.search(clean):
        return True
    if NETEASE_ALBUM_URL_RE.search(clean):
        return True
    if NETEASE_PLAYLIST_URL_RE.search(clean):
        return True
    return False


async def resolve_short_url(short_url: str, timeout: int = 10) -> Optional[str]:
    """
    解析 163cn.tv 短链接，跟随重定向获取真实 URL。

    Returns:
        重定向后的真实 URL，解析失败返回 None
    """
    t_start = time.time()
    logger.info("[Netease] 解析短链接 → %s", short_url)

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=5.0),
            follow_redirects=True,
            max_redirects=5,
        ) as client:
            resp = await client.get(short_url, headers={"User-Agent": USER_AGENT})
            final_url = str(resp.url)
            elapsed = time.time() - t_start

            if final_url and final_url != short_url:
                logger.info(
                    "[Netease] 短链接解析成功 (%.2fs) → %s → %s",
                    elapsed, short_url, final_url,
                )
                return final_url
            else:
                logger.warning(
                    "[Netease] 短链接未重定向 (%.2fs) → %s",
                    elapsed, short_url,
                )
                return None
    except httpx.TimeoutException:
        logger.error(
            "[Netease] 短链接解析超时 (%.1fs) → %s (timeout=%ds)",
            time.time() - t_start, short_url, timeout,
        )
        return None
    except httpx.HTTPError as e:
        logger.error(
            "[Netease] 短链接解析 HTTP 错误 (%.1fs) → %s: %s",
            time.time() - t_start, short_url, e,
        )
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
        song_id = match.group("id_path") or match.group("id_query")
        return song_id
    return None


async def classify_links(event: MessageEvent) -> ParsedLinks:
    """
    一次性从消息事件中分类提取所有网易云链接。

    处理流程：
    1. 从消息正文和卡片元数据中提取所有 URL（一次）
    2. 直接匹配 song/album/playlist/program 四类 URL → 提取 ID
    3. 163cn.tv 短链接 → 只跟随重定向一次 → 对目标 URL 分类
    4. 去重返回

    相比旧的 4 个 extract_*_ids_from_event，短链接只解析一次，
    避免同一短链接被多次 resolve 成 4 次 HTTP 重定向。
    """
    result = ParsedLinks()
    all_urls = extract_all_urls(event)
    if not all_urls:
        return result

    seen: dict[str, set[str]] = {
        "song": set(), "album": set(), "playlist": set(), "program": set(),
    }

    def _add(kind: str, id_: str) -> None:
        if id_ and id_ not in seen[kind]:
            seen[kind].add(id_)
            getattr(result, f"{kind}_ids").append(id_)

    def _classify_url(url: str) -> bool:
        """对单个 URL 尝试四类匹配，命中即返回 True。"""
        song_id = extract_song_id_from_url(url)
        if song_id:
            _add("song", song_id)
            return True
        album_id = extract_album_id_from_url(url)
        if album_id:
            _add("album", album_id)
            return True
        playlist_id = extract_playlist_id_from_url(url)
        if playlist_id:
            _add("playlist", playlist_id)
            return True
        m = NETEASE_PROGRAM_URL_RE.search(url)
        if m:
            _add("program", m.group("id"))
            return True
        return False

    short_urls_to_resolve: list[str] = []
    for url in all_urls:
        if NETEASE_SHORT_URL_RE.match(url):
            short_urls_to_resolve.append(url)
            continue
        _classify_url(url)

    for short_url in short_urls_to_resolve:
        resolved = await resolve_short_url(short_url)
        if resolved:
            _classify_url(resolved)

    logger.info(
        "[Netease] 链接分类完成 → song=%d album=%d playlist=%d program=%d",
        len(result.song_ids), len(result.album_ids),
        len(result.playlist_ids), len(result.program_ids),
    )
    return result


def extract_album_id_from_url(url: str) -> Optional[str]:
    """
    从 URL 中提取专辑 ID。

    支持格式：
    - https://music.163.com/album/387568337
    - https://y.music.163.com/m/album?id=387568337
    """
    match = NETEASE_ALBUM_URL_RE.search(url)
    if match:
        album_id = match.group("id_path") or match.group("id_query")
        return album_id
    return None


def extract_program_ids(text: str) -> list[str]:
    """从文本中提取播客/电台节目 ID（去重，保持顺序）。"""
    ids: list[str] = []
    seen: set[str] = set()
    clean = _unescape_cq(text)
    for match in NETEASE_PROGRAM_URL_RE.finditer(clean):
        pid = match.group("id")
        if pid and pid not in seen:
            seen.add(pid)
            ids.append(pid)
    return ids


def extract_playlist_id_from_url(url: str) -> Optional[str]:
    """
    从 URL 中提取歌单 ID。

    支持格式：
    - https://music.163.com/playlist/18147720055
    - https://music.163.com/m/playlist?id=18147720055
    """
    match = NETEASE_PLAYLIST_URL_RE.search(url)
    if match:
        playlist_id = match.group("id_path") or match.group("id_query")
        return playlist_id
    return None


def extract_playlist_ids(text: str) -> list[str]:
    """从文本中提取歌单 ID（去重，保持顺序）。"""
    ids: list[str] = []
    seen: set[str] = set()
    clean = _unescape_cq(text)
    for match in NETEASE_PLAYLIST_URL_RE.finditer(clean):
        pid = match.group("id_path") or match.group("id_query")
        if pid and pid not in seen:
            seen.add(pid)
            ids.append(pid)
    return ids


