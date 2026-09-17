"""
QQ 音乐接口模块。

只依赖 ``u.y.qq.com/cgi-bin/musicu.fcg`` 的 ``get_song_detail_yqq``，用途有两个：

1. **把数字 songid 换算成 songmid**。yt-dlp 只认 songmid；把数字塞进 ``song_mid``
   字段时 QQ 接口会返回一个字段全空却 **不报错** 的 track_info，yt-dlp 随后抛出
   误导性的 "only available for registered users"。所以换算必须自己做。
2. **读取付费标志与文件大小**，用于在 yt-dlp 报「没有可用格式」时给出准确原因
   （VIP 付费 / 缺 cookie / 确实下架）。

接口契约（2026-09 实测）::

    POST https://u.y.qq.com/cgi-bin/musicu.fcg
    {"comm": {"ct": 24, "cv": 0, "format": "json", "uin": <uin或0>},
     "info": {"module": "music.pf_song_detail_svr",
              "method": "get_song_detail_yqq",
              "param": {"song_type": 0, "song_mid": "<14位>"}}}      # 或 "song_id": <数字>

    → {"info": {"code": 0, "data": {"track_info": {...}}}}

``track_info`` 关键字段：``mid``、``name``、``album.name``、``singer[].name``、
``interval``、``file.size_128mp3``、``pay.pay_play``（1 = 播放需付费/会员）。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .errors import QQMusicError, QQMusicResolveError
from .parser import QQSongRef

logger = logging.getLogger("HikariBot.QQMusicApi")

MUSICU_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.7103.48 Safari/537.36"
)

# 关注的音质档位 -> track_info.file 里的体积字段名
SIZE_FIELDS: dict[str, str] = {
    "flac": "size_flac",
    "ape": "size_ape",
    "320mp3": "size_320mp3",
    "128mp3": "size_128mp3",
    "96aac": "size_96aac",
    "48aac": "size_48aac",
}


@dataclass(slots=True)
class QQTrackDetail:
    """一首歌的详情（来自 QQ 音乐接口）。"""

    songmid: str
    name: str = ""
    singers: tuple[str, ...] = ()
    album: str = ""
    interval: int = 0
    pay_play: int = 0
    pay_month: int = 0
    sizes: dict[str, int] = field(default_factory=dict)

    @property
    def singer_text(self) -> str:
        return "、".join(self.singers) if self.singers else "未知歌手"

    @property
    def album_text(self) -> str:
        return self.album or "未知专辑"

    @property
    def vip_only(self) -> bool:
        """是否属于「播放也需付费/会员」的曲目。

        ``pay_play`` 是两个 ID 形态里唯一能区分「匿名可下标准音质」与「匿名全空」
        的字段：免费曲 pay_play=0，VIP 曲 pay_play=1。
        """
        return self.pay_play == 1


# =========================
# Cookie
# =========================

_cookie_cache: dict[str, tuple[int, int, dict[str, str]]] = {}
_cookie_lock = threading.Lock()


def parse_netscape_cookies(text: str) -> dict[str, str]:
    """解析 Netscape / curl 格式 cookie 文件，返回 ``name -> value``。

    处理要点：

    - 以 ``#`` 开头的是注释，直接跳过；
    - 但 ``#HttpOnly_`` 前缀是**真实 cookie 行**，需要去掉前缀后再解析；
    - 每行是 7 个 TAB 分隔字段，值是第 7 个（**允许为空**，如 ``_qimei_q36``）；
    - 只保留 ``qq.com`` 域的 cookie，避免把同文件里其它站点的凭据发给 QQ。
    """
    jar: dict[str, str] = {}

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r\n")
        if not line.strip():
            continue
        if line.startswith("#"):
            if line.startswith("#HttpOnly_"):
                line = line[len("#HttpOnly_"):]
            else:
                continue

        parts = line.split("\t")
        if len(parts) < 7:
            continue

        domain, name, value = parts[0].strip().lower(), parts[5].strip(), parts[6]
        if not name:
            continue
        if not (domain == "qq.com" or domain.endswith(".qq.com")):
            continue
        jar[name] = value

    return jar


def load_cookie_jar(cookiefile: Path | None) -> dict[str, str]:
    """读取并缓存 cookie 文件（按 mtime + size 失效）。文件不存在返回空字典。"""
    if cookiefile is None:
        return {}
    try:
        stat = cookiefile.stat()
    except OSError:
        return {}

    cache_key = str(cookiefile)
    with _cookie_lock:
        cached = _cookie_cache.get(cache_key)
        if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
            return dict(cached[2])

    try:
        text = cookiefile.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("[QQMusic] cookie 文件读取失败 → %s: %s", cookiefile, exc)
        return {}

    jar = parse_netscape_cookies(text)
    with _cookie_lock:
        _cookie_cache[cache_key] = (stat.st_mtime_ns, stat.st_size, dict(jar))
    logger.info("[QQMusic] 已加载 cookie 文件 → %s（%d 项）", cookiefile, len(jar))
    return jar


def cookie_header(jar: dict[str, str]) -> str:
    """把 cookie 字典拼成 Cookie 请求头。"""
    return "; ".join(f"{name}={value}" for name, value in jar.items() if name)


def _uin_from_jar(jar: dict[str, str]) -> int:
    raw = (jar.get("uin") or "").lstrip("o").strip()
    return int(raw) if raw.isdigit() else 0


# =========================
# 接口调用
# =========================


def _sizes_from_file(file_info: Any) -> dict[str, int]:
    """从 ``track_info.file`` 提取各档位体积（缺失或非正数记为 0）。"""
    if not isinstance(file_info, dict):
        return {}
    sizes: dict[str, int] = {}
    for quality, field_name in SIZE_FIELDS.items():
        value = file_info.get(field_name)
        sizes[quality] = int(value) if isinstance(value, int) and value > 0 else 0
    return sizes


def _detail_from_track(track: Any) -> QQTrackDetail:
    """把接口返回的 track_info 转成 QQTrackDetail。"""
    if not isinstance(track, dict):
        raise QQMusicResolveError("歌曲信息返回格式异常。")

    songmid = str(track.get("mid") or "").strip()
    if not songmid:
        # 典型场景：数字 songid 被当作 song_mid 传入。接口不报错，只回空壳。
        raise QQMusicResolveError("无法把该 ID 解析成有效的歌曲信息。")

    singers_raw = track.get("singer")
    singers: tuple[str, ...] = ()
    if isinstance(singers_raw, list):
        singers = tuple(
            str(item.get("name")).strip()
            for item in singers_raw
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        )

    album_raw = track.get("album")
    album = ""
    if isinstance(album_raw, dict):
        album = str(album_raw.get("name") or "").strip()

    pay_raw = track.get("pay")
    pay = pay_raw if isinstance(pay_raw, dict) else {}

    def _as_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    return QQTrackDetail(
        songmid=songmid,
        name=str(track.get("name") or track.get("title") or "").strip(),
        singers=singers,
        album=album,
        interval=_as_int(track.get("interval")),
        pay_play=_as_int(pay.get("pay_play")),
        pay_month=_as_int(pay.get("pay_month")),
        sizes=_sizes_from_file(track.get("file")),
    )


async def fetch_song_detail(
    cfg: dict[str, Any],
    *,
    songmid: str = "",
    songid: str = "",
) -> QQTrackDetail:
    """查询歌曲详情。

    Args:
        songmid: 14 位字母数字 ID（优先）。
        songid : 纯数字 ID，由接口换算成 songmid。

    Raises:
        QQMusicResolveError: ID 无法解析成有效歌曲。
        QQMusicError: 接口调用失败。
    """
    param: dict[str, Any] = {"song_type": 0}
    if songmid:
        param["song_mid"] = songmid
    elif songid and songid.isdigit():
        param["song_id"] = int(songid)
    else:
        raise QQMusicResolveError("链接里没有可用的歌曲 ID。")

    from .config import get_cookiefile

    jar = load_cookie_jar(get_cookiefile(cfg))
    payload = {
        "comm": {
            "ct": 24,
            "cv": 0,
            "format": "json",
            "uin": _uin_from_jar(jar),
        },
        "info": {
            "module": "music.pf_song_detail_svr",
            "method": "get_song_detail_yqq",
            "param": param,
        },
    }
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": "https://y.qq.com/",
        "Content-Type": "application/json",
    }
    header = cookie_header(jar)
    if header:
        headers["Cookie"] = header

    timeout = max(5, int(cfg.get("api_timeout", 30)))
    ident = songmid or songid

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=8.0)) as client:
            resp = await client.post(MUSICU_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException as exc:
        raise QQMusicError(f"查询歌曲信息超时（{timeout}s）。") from exc
    except httpx.HTTPError as exc:
        raise QQMusicError(f"查询歌曲信息失败：{exc}") from exc
    except ValueError as exc:  # resp.json() 解析失败
        raise QQMusicError("歌曲信息返回格式异常。") from exc

    info = data.get("info") if isinstance(data, dict) else None
    if not isinstance(info, dict):
        raise QQMusicError("歌曲信息返回格式异常。")

    code = info.get("code")
    payload_data = info.get("data") if isinstance(info.get("data"), dict) else {}
    track = payload_data.get("track_info")

    if not isinstance(track, dict) or not str(track.get("mid") or "").strip():
        logger.warning(
            "[QQMusic] track_info 为空 → id=%s, code=%s, songid=%s",
            ident, code, bool(songid),
        )
        raise QQMusicResolveError("没有找到这首歌，可能已下架或链接有误。")

    detail = _detail_from_track(track)
    logger.info(
        "[QQMusic] 歌曲详情 → %s | %s | album=%s | %ds | pay_play=%s | sizes=%s",
        detail.songmid,
        detail.name,
        detail.album,
        detail.interval,
        detail.pay_play,
        {k: v for k, v in detail.sizes.items() if v},
    )
    return detail


async def fetch_track_detail(ref: QQSongRef, cfg: dict[str, Any]) -> QQTrackDetail:
    """按引用查询详情：songmid 直查，songid 交给接口换算。"""
    if ref.songmid:
        return await fetch_song_detail(cfg, songmid=ref.songmid)
    if ref.songid:
        return await fetch_song_detail(cfg, songid=ref.songid)
    raise QQMusicResolveError("链接里没有可用的歌曲 ID。")
