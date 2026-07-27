"""
网易云音乐 API 调用模块。

负责：
1. 调用 api-enhanced 服务器获取歌曲信息和 MP3 下载链接
2. 调用 /song/detail, /song/url, /dj/program/detail, /album 接口
"""

import logging
import time
from urllib.parse import quote

import httpx

from .parser import USER_AGENT, NeteaseSongInfo, NeteaseSongUrlResult

logger = logging.getLogger("HikariBot.NeteaseParser")


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


async def fetch_album_detail(
    album_id: str,
    api_base: str,
    timeout: int = 30,
    real_ip: str = "",
) -> tuple[str, list[NeteaseSongInfo]]:
    """
    获取专辑详情，包括专辑名和曲目列表。

    调用 /album?id=XXX API，返回 (album_name, songs_list)。

    Raises:
        httpx.TimeoutException: API 请求超时
        httpx.HTTPStatusError: HTTP 状态码异常
        ValueError: 响应格式异常 / 专辑不存在
    """
    path = f"/album?id={album_id}"
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

    songs_raw = data.get("songs", [])
    if not songs_raw:
        raise ValueError(f"专辑不存在或为空 (id={album_id})")

    # 从第一首歌提取专辑名
    album_name = ""
    first = songs_raw[0]
    al = first.get("al") or {}
    if isinstance(al, dict):
        album_name = str(al.get("name", ""))

    songs: list[NeteaseSongInfo] = []
    for s in songs_raw:
        artists = s.get("ar") or s.get("artists") or []
        artist_names = " / ".join(
            a.get("name", "") for a in artists if isinstance(a, dict)
        )
        album_info = s.get("al") or s.get("album") or {}
        album_name_for_song = album_info.get("name", "") if isinstance(album_info, dict) else ""

        songs.append(NeteaseSongInfo(
            id=str(s.get("id", "")),
            name=str(s.get("name", "")),
            artist=artist_names,
            album=album_name_for_song,
            pic_url=album_info.get("picUrl", "") if isinstance(album_info, dict) else "",
        ))

    logger.info(
        "[Netease] API album/detail 响应 (%.1fs) → HTTP %d, 专辑=%s, 曲目=%d 首",
        elapsed, resp.status_code, album_name, len(songs),
    )
    return album_name, songs
