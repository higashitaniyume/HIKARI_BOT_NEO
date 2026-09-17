"""
QQ 音乐链接与分享卡片解析模块。

负责从消息正文和 QQ 分享卡片里提取 QQ 音乐歌曲引用，并归一化成 yt-dlp 能识别的
`songDetail` 链接。

QQ 音乐有两套歌曲 ID，二者不可互换：

- **songmid**：14 位字母数字（如 ``003dKInI1dmvj6``），yt-dlp 的 qqmusic 提取器直接支持。
- **songid** ：纯数字（如 ``587897337``），yt-dlp 不支持。它会被当成 song_mid 传给
  QQ 接口，接口返回一个**字段全空但不报错**的 track_info，最终导致 yt-dlp 抛出
  误导性的 "only available for registered users"。必须先查接口换成 songmid。

真实分享卡片里两种都会出现，例如::

    https://i.y.qq.com/v8/playsong.html?...&songmid=003dKInI1dmvj6&...
    https://i.y.qq.com/v8/playsong.html?songid=587897337#webchat_redirect

另外 `i.y.qq.com/v8/playsong.html` 会 302 到 `y.qq.com/n/ryqq_v2/songDetail/...`，
而 yt-dlp 只认 `ryqq`（非 `_v2`），所以卡片里的 jumpUrl 必须归一化后才能交给 yt-dlp。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

# =========================
# 正则
# =========================

# songmid / songid 查询参数（i.y.qq.com/v8/playsong.html 等）
_SONGMID_QUERY_RE = re.compile(r"[?&#]songmid=([0-9A-Za-z]{6,20})", re.IGNORECASE)
_SONGID_QUERY_RE = re.compile(r"[?&#]songid=(\d{3,15})", re.IGNORECASE)

# y.qq.com/n/ryqq/songDetail/<id>，兼容 ryqq_v2 与 /n/yqq/ 变体
_SONGDETAIL_RE = re.compile(
    r"y\.qq\.com/(?:n/)?(?:ryqq(?:_v2)?|yqq)/songDetail/(?P<id>[0-9A-Za-z]{6,20})",
    re.IGNORECASE,
)

# 旧版 y.qq.com/n/yqq/song/<id>.html
_LEGACY_SONG_RE = re.compile(
    r"y\.qq\.com/(?:n/)?(?:yqq|ryqq)/song/(?P<id>[0-9A-Za-z]{6,20})(?:\.html)?",
    re.IGNORECASE,
)

# 卡片 JSON 里的 music 段（用于识别「这是 QQ 音乐卡片」）
_QQMUSIC_CARD_APP_RE = re.compile(r'"app"\s*:\s*"com\.tencent\.music\.lua"', re.IGNORECASE)

_YTDLP_SONG_URL = "https://y.qq.com/n/ryqq/songDetail/{songmid}"


@dataclass(frozen=True, slots=True)
class QQSongRef:
    """一条 QQ 音乐歌曲引用。

    二者必有其一非空：``songmid`` 可直接交给 yt-dlp；``songid`` 需要先查接口换算。
    """

    songmid: str = ""
    songid: str = ""
    source: str = ""

    @property
    def key(self) -> str:
        """用于去重与缓存的身份标识。"""
        return self.songmid or self.songid

    @property
    def needs_resolve(self) -> bool:
        """是否需要调接口把 songid 换成 songmid。"""
        return not self.songmid and bool(self.songid)


def _unescape(text: str) -> str:
    """还原 CQ 码转义与 JSON 斜杠转义，方便正则直接扫卡片原文。"""
    return (
        text.replace("&#44;", ",")
        .replace("&#91;", "[")
        .replace("&#93;", "]")
        .replace("&amp;", "&")
        .replace("\\/", "/")
    )


def classify_song_id(raw_id: str) -> QQSongRef:
    """把裸 ID 分类成 songmid 或 songid。

    QQ 的 songmid 是 14 位 base62（一定含字母），纯数字 ID 必然是 songid，
    因此用「是否全为数字」判别即可。
    """
    value = (raw_id or "").strip()
    if not value:
        return QQSongRef()
    if value.isdigit():
        return QQSongRef(songid=value)
    return QQSongRef(songmid=value)


def extract_song_refs(text: str) -> list[QQSongRef]:
    """从任意字符串（消息正文或卡片 JSON 原文）中提取 QQ 音乐歌曲引用。

    结果按引用在文本中**出现的先后**排序，同一个歌曲 ID 只保留一次。

    注意不能按「正则分组」顺序收集：一张卡片里 ``songmid=`` 出现在卡片中间，
    而正文里的 ``songDetail/xxx`` 可能在更前面，分组收集会让处理顺序和用户
    实际发送的顺序对不上。
    """
    if not text:
        return []

    clean = _unescape(text)

    # (出现位置, 来源, 裸 ID)
    hits: list[tuple[int, str, str]] = []
    for match in _SONGMID_QUERY_RE.finditer(clean):
        hits.append((match.start(), "query", match.group(1)))
    for match in _SONGID_QUERY_RE.finditer(clean):
        hits.append((match.start(), "query", match.group(1)))
    for match in _SONGDETAIL_RE.finditer(clean):
        hits.append((match.start(), "songdetail", match.group("id")))
    for match in _LEGACY_SONG_RE.finditer(clean):
        hits.append((match.start(), "legacy", match.group("id")))

    hits.sort(key=lambda item: item[0])

    refs: list[QQSongRef] = []
    seen: set[str] = set()
    for _, source, raw_id in hits:
        ref = classify_song_id(raw_id)
        if not ref.key or ref.key in seen:
            continue
        seen.add(ref.key)
        refs.append(QQSongRef(songmid=ref.songmid, songid=ref.songid, source=source))

    return refs


def has_qqmusic_ref(text: str) -> bool:
    """文本中是否包含 QQ 音乐歌曲引用。"""
    return bool(extract_song_refs(text))


def _json_segment_texts(event: Any) -> list[str]:
    """取出事件里所有 json 消息段的原始 JSON 文本。"""
    texts: list[str] = []
    for segment in getattr(event, "message", None) or []:
        if getattr(segment, "type", "") != "json":
            continue
        data = getattr(segment, "data", None)
        if not isinstance(data, dict):
            continue
        raw = data.get("data")
        if isinstance(raw, str) and raw.strip():
            texts.append(raw)
    return texts


def _event_texts(event: Any) -> list[str]:
    """事件中所有需要扫描的文本：消息正文 + 卡片 JSON 原文。"""
    texts: list[str] = []
    try:
        body = str(event.get_message())
    except Exception:
        body = ""
    if body:
        texts.append(body)
    texts.extend(_json_segment_texts(event))
    return texts


def collect_song_refs(event: Any) -> list[QQSongRef]:
    """从消息事件（正文 + QQ 卡片）中收集去重后的歌曲引用。"""
    refs: list[QQSongRef] = []
    seen: set[str] = set()
    for text in _event_texts(event):
        for ref in extract_song_refs(text):
            if ref.key in seen:
                continue
            seen.add(ref.key)
            refs.append(ref)
    return refs


def is_qqmusic_card_event(event: Any) -> bool:
    """事件里是否含 QQ 音乐分享卡片（app=com.tencent.music.lua）。"""
    return any(_QQMUSIC_CARD_APP_RE.search(text) for text in _json_segment_texts(event))


def build_song_url(songmid: str) -> str:
    """把 songmid 归一化为 yt-dlp 能识别的 songDetail 链接。"""
    return _YTDLP_SONG_URL.format(songmid=songmid)


def dedupe_refs(refs: Iterable[QQSongRef]) -> list[QQSongRef]:
    """按 ID 去重，保持原有顺序。"""
    out: list[QQSongRef] = []
    seen: set[str] = set()
    for ref in refs:
        if not ref.key or ref.key in seen:
            continue
        seen.add(ref.key)
        out.append(ref)
    return out
