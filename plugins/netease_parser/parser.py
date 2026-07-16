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
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote

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

# 通用 URL 提取
GENERIC_URL_RE = re.compile(r"https?://[^\s\"'>]+", re.IGNORECASE)

# 匹配网易云音乐播客/电台节目链接
# 格式：https://y.music.163.com/m/program?id=2538607775
NETEASE_PROGRAM_URL_RE = re.compile(
    r"(?:https?://)?(?:y\.)?music\.163\.com"
    r"(?:/m)?/program\?(?:[^\s]*?&)?id=(?P<id>\d{5,12})",
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
    if NETEASE_PROGRAM_URL_RE.search(text):
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


async def extract_song_ids_from_event(event: MessageEvent) -> list[str]:
    """
    从消息事件中提取所有网易云音乐歌曲 ID。

    处理流程：
    1. 从消息正文和卡片元数据中提取所有 URL
    2. 直接匹配 music.163.com/song/... 格式 → 提取 ID
    3. 匹配 163cn.tv 短链接 → 跟随重定向 → 从目标 URL 提取 ID
    4. 去重返回
    """
    t_start = time.time()
    ids: list[str] = []
    seen_ids: set[str] = set()
    short_urls_to_resolve: list[str] = []

    all_urls = extract_all_urls(event)

    if not all_urls:
        logger.debug("[Netease] 消息中未提取到任何 URL")
        return []

    logger.info("[Netease] 从消息中提取到 %d 个 URL", len(all_urls))

    for url in all_urls:
        # 尝试直接匹配 music.163.com/song/...
        song_id = extract_song_id_from_url(url)
        if song_id:
            if song_id not in seen_ids:
                seen_ids.add(song_id)
                ids.append(song_id)
                logger.debug("[Netease] 直接提取到歌曲 ID → %s (%s)", song_id, url[:60])
            continue

        # 匹配 163cn.tv 短链接
        if NETEASE_SHORT_URL_RE.match(url):
            short_urls_to_resolve.append(url)
            logger.debug("[Netease] 发现短链接 → %s", url)

    # 批量解析短链接
    if short_urls_to_resolve:
        logger.info(
            "[Netease] 解析 %d 个 163cn.tv 短链接...", len(short_urls_to_resolve),
        )
        for short_url in short_urls_to_resolve:
            resolved = await resolve_short_url(short_url)
            if resolved:
                song_id = extract_song_id_from_url(resolved)
                if song_id and song_id not in seen_ids:
                    seen_ids.add(song_id)
                    ids.append(song_id)
                    logger.info(
                        "[Netease] 短链接解析 → 提取到歌曲 ID: %s → %s",
                        short_url, song_id,
                    )
                else:
                    logger.warning(
                        "[Netease] 短链接解析后未找到歌曲 ID → %s → %s",
                        short_url, resolved,
                    )
    else:
        logger.debug("[Netease] 无需解析短链接")

    elapsed = time.time() - t_start
    if ids:
        logger.info(
            "[Netease] 歌曲 ID 提取完成 (%.2fs) → 共 %d 个: %s",
            elapsed, len(ids), ids,
        )
    else:
        logger.info(
            "[Netease] 歌曲 ID 提取完成 (%.2fs) → 未找到有效歌曲 ID",
            elapsed,
        )

    return ids


async def extract_program_ids_from_event(event: MessageEvent) -> list[str]:
    """
    从消息事件中提取所有播客/电台节目 ID。

    处理流程：
    1. 从消息正文和卡片元数据中提取所有 URL
    2. 直接匹配 program URL → 提取 ID
    3. 匹配 163cn.tv 短链接 → 跟随重定向 → 从目标 URL 提取 ID
    4. 去重返回
    """
    t_start = time.time()
    ids: list[str] = []
    seen_ids: set[str] = set()
    short_urls_to_resolve: list[str] = []

    all_urls = extract_all_urls(event)

    if not all_urls:
        return []

    logger.info("[Netease] 从消息中提取到 %d 个 URL（播客）", len(all_urls))

    for url in all_urls:
        # 尝试直接匹配 program URL
        match = NETEASE_PROGRAM_URL_RE.search(url)
        if match:
            pid = match.group("id")
            if pid and pid not in seen_ids:
                seen_ids.add(pid)
                ids.append(pid)
                logger.debug("[Netease] 直接提取到播客 ID → %s (%s)", pid, url[:60])
            continue

        # 匹配 163cn.tv 短链接
        if NETEASE_SHORT_URL_RE.match(url):
            short_urls_to_resolve.append(url)

    # 批量解析短链接
    if short_urls_to_resolve:
        logger.info("[Netease] 解析 %d 个短链接（播客）...", len(short_urls_to_resolve))
        for short_url in short_urls_to_resolve:
            resolved = await resolve_short_url(short_url)
            if resolved:
                match = NETEASE_PROGRAM_URL_RE.search(resolved)
                if match:
                    pid = match.group("id")
                    if pid and pid not in seen_ids:
                        seen_ids.add(pid)
                        ids.append(pid)
                        logger.info("[Netease] 短链接解析 → 提取到播客 ID: %s → %s", short_url, pid)

    elapsed = time.time() - t_start
    if ids:
        logger.info("[Netease] 播客 ID 提取完成 (%.2fs) → 共 %d 个: %s", elapsed, len(ids), ids)
    else:
        logger.debug("[Netease] 播客 ID 提取完成 (%.2fs) → 未找到", elapsed)

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

    Raises:
        httpx.TimeoutException: API 请求超时
        httpx.HTTPStatusError: HTTP 状态码异常
        ValueError: 响应格式异常 / 歌曲不存在
    """
    path = f"/song/detail?ids={song_id}"
    if real_ip:
        path += f"&realIP={real_ip}"

    url = _api_url(api_base, path)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    t_start = time.time()
    logger.info(
        "[Netease] API GET → %s (timeout=%ds, real_ip=%s)",
        url, timeout, "已配置" if real_ip else "未配置",
    )

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        elapsed = time.time() - t_start
        logger.error(
            "[Netease] API 超时 (%.1fs) → %s（请检查 api_base_url 是否可访问）",
            elapsed, url,
        )
        raise
    except httpx.HTTPStatusError as e:
        elapsed = time.time() - t_start
        logger.error(
            "[Netease] API HTTP 错误 (%.1fs) → %s HTTP %s",
            elapsed, url, e.response.status_code,
        )
        raise
    except Exception:
        elapsed = time.time() - t_start
        logger.error("[Netease] API 请求异常 (%.1fs) → %s", elapsed, url)
        raise

    elapsed = time.time() - t_start

    if data.get("code") != 200:
        raise ValueError(
            f"API 返回异常: code={data.get('code')}, msg={data.get('msg', '')} "
            f"(elapsed={elapsed:.1f}s)"
        )

    songs = data.get("songs", [])
    if not songs:
        logger.warning("[Netease] API 响应正常但歌曲列表为空 → id=%s (%.1fs)", song_id, elapsed)
        raise ValueError("歌曲不存在")

    song = songs[0]
    # 网易云 API 字段名：ar=artists, al=album
    artists = song.get("ar") or song.get("artists") or []
    artist_names = " / ".join(
        a.get("name", "") for a in artists if isinstance(a, dict)
    )
    album_info = song.get("al") or song.get("album") or {}
    album_name = album_info.get("name", "") if isinstance(album_info, dict) else ""

    result = NeteaseSongInfo(
        id=str(song.get("id", song_id)),
        name=str(song.get("name", "")),
        artist=artist_names,
        album=album_name,
        pic_url=album_info.get("picUrl", "") if isinstance(album_info, dict) else "",
    )

    logger.info(
        "[Netease] API song/detail 响应 (%.1fs) → HTTP %d, 歌曲=%s, 歌手=%s",
        elapsed, resp.status_code, result.name, result.artist,
    )
    return result


def extract_program_ids(text: str) -> list[str]:
    """从文本中提取播客/电台节目 ID（去重，保持顺序）。"""
    ids: list[str] = []
    seen: set[str] = set()
    for match in NETEASE_PROGRAM_URL_RE.finditer(text):
        pid = match.group("id")
        if pid and pid not in seen:
            seen.add(pid)
            ids.append(pid)
    return ids


async def fetch_program_detail(
    program_id: str,
    api_base: str,
    timeout: int = 30,
    real_ip: str = "",
    cookie: str = "",
) -> NeteaseSongInfo:
    """
    获取播客/电台节目详细信息。

    播客节目的音频实际是 mainSong，通过此接口获取其 song_id 后
    再调用 fetch_song_url 获取音频链接。

    Returns:
        NeteaseSongInfo 对象，其中 id 是 mainSong.id（用于后续获取音频 URL）

    Raises:
        httpx.TimeoutException: API 请求超时
        httpx.HTTPStatusError: HTTP 状态码异常
        ValueError: 响应格式异常
    """
    path = f"/dj/program/detail?id={program_id}"
    if real_ip:
        path += f"&realIP={real_ip}"
    if cookie:
        path += f"&cookie={quote(cookie)}"

    url = _api_url(api_base, path)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    t_start = time.time()
    logger.info(
        "[Netease] API GET → %s (timeout=%ds, cookie=%s)",
        url, timeout, "已配置" if cookie else "未配置",
    )

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        elapsed = time.time() - t_start
        logger.error("[Netease] API 超时 (%.1fs) → %s", elapsed, url)
        raise
    except httpx.HTTPStatusError as e:
        elapsed = time.time() - t_start
        logger.error("[Netease] API HTTP 错误 (%.1fs) → %s HTTP %s", elapsed, url, e.response.status_code)
        raise
    except Exception:
        elapsed = time.time() - t_start
        logger.error("[Netease] API 请求异常 (%.1fs) → %s", elapsed, url)
        raise

    elapsed = time.time() - t_start

    if data.get("code") != 200:
        raise ValueError(
            f"API 返回异常: code={data.get('code')}, msg={data.get('msg', '')} "
            f"(elapsed={elapsed:.1f}s)"
        )

    program = data.get("program")
    if not program:
        raise ValueError("播客节目不存在")

    main_song = program.get("mainSong") or {}
    song_id = str(main_song.get("id", ""))
    name = str(main_song.get("name", program.get("name", "")) or "")
    artists_list = main_song.get("artists") or program.get("artists") or []
    artist_names = " / ".join(
        a.get("name", "") for a in artists_list if isinstance(a, dict)
    )
    radio = program.get("radio") or {}
    album_name = str(radio.get("name", "") or "")

    result = NeteaseSongInfo(
        id=song_id,
        name=name,
        artist=artist_names,
        album=album_name,
    )

    logger.info(
        "[Netease] API program/detail 响应 (%.1fs) → HTTP %d, 节目=%s, 歌手=%s, mainSong.id=%s",
        elapsed, resp.status_code, result.name, result.artist, song_id,
    )
    return result


async def fetch_song_url(
    song_id: str,
    api_base: str,
    timeout: int = 30,
    real_ip: str = "",
    high_quality: bool = True,
    cookie: str = "",
) -> NeteaseSongUrlResult:
    """
    获取歌曲音频下载 URL。

    根据 high_quality 参数请求不同码率：
    - True:  br=999000（最高可用，FLAC > 320k > 192k）
    - False: br=320000（320kbps MP3）

    如需解析 VIP 歌曲的完整音频，请传入已登录网易云账号的 cookie。

    Raises:
        httpx.TimeoutException: API 请求超时
        httpx.HTTPStatusError: HTTP 状态码异常
        ValueError: 响应格式异常
    """
    path = f"/song/url?id={song_id}"
    if high_quality:
        path += "&br=999000"
    else:
        path += "&br=320000"
    if real_ip:
        path += f"&realIP={real_ip}"
    if cookie:
        path += f"&cookie={quote(cookie)}"

    url = _api_url(api_base, path)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    t_start = time.time()
    logger.info(
        "[Netease] API GET → %s (timeout=%ds)",
        url, timeout,
    )

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        elapsed = time.time() - t_start
        logger.error(
            "[Netease] API 超时 (%.1fs) → %s（请检查 api_base_url 是否可访问）",
            elapsed, url,
        )
        raise
    except httpx.HTTPStatusError as e:
        elapsed = time.time() - t_start
        logger.error(
            "[Netease] API HTTP 错误 (%.1fs) → %s HTTP %s",
            elapsed, url, e.response.status_code,
        )
        raise
    except Exception:
        elapsed = time.time() - t_start
        logger.error("[Netease] API 请求异常 (%.1fs) → %s", elapsed, url)
        raise

    elapsed = time.time() - t_start

    if data.get("code") != 200:
        raise ValueError(
            f"API 返回异常: code={data.get('code')}, msg={data.get('msg', '')} "
            f"(elapsed={elapsed:.1f}s)"
        )

    items = data.get("data", [])
    if not items:
        logger.warning("[Netease] API song/url 返回空 data → id=%s (%.1fs)", song_id, elapsed)
        return NeteaseSongUrlResult(code=404)

    item = items[0]
    audio_url = str(item.get("url") or "")
    br = int(item.get("br", 0))
    size = int(item.get("size", 0))

    if not audio_url:
        logger.warning(
            "[Netease] API song/url URL 为空 → id=%s, code=%s, 可能需要版权/登录 (%.1fs)",
            song_id, item.get("code"), elapsed,
        )
    else:
        logger.info(
            "[Netease] API song/url 响应 (%.1fs) → HTTP %d, br=%skbps, size=%.1fMB",
            elapsed, resp.status_code, br // 1000, size / 1024 / 1024,
        )

    return NeteaseSongUrlResult(
        url=audio_url,
        br=br,
        size=size,
        type=str(item.get("type", "mp3")),
        code=int(item.get("code", 200)),
    )
