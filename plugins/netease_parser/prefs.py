"""
网易云音乐解析格式偏好与最近发送记录。

1. 用户格式偏好（mp3/flac）持久化到 UserData/netease_prefs.json
   - 无偏好用户默认 FLAC
   - 偏好 mp3 的用户解析时按 320k MP3 请求
2. 最近发送记录（仅内存，重启即清）用于「回复 mp3/flac 换格式重发」：
   - 发送歌曲/专辑时记录 bot 发出的消息 ID 与内容
   - 用户回复 bot 消息说 mp3/flac 时，按被回复的 message_id 找到对应记录并重发
"""

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("HikariBot.NeteasePrefs")

PREFS_PATH = Path("UserData/netease_prefs.json")

_lock = threading.RLock()

DEFAULT_QUALITY = "flac"
QUALITIES = ("flac", "mp3")


def _read_all() -> dict[str, Any]:
    if not PREFS_PATH.exists():
        return {}
    try:
        data = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_all(data: dict[str, Any]) -> None:
    PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = PREFS_PATH.with_name(
        f"{PREFS_PATH.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, PREFS_PATH)


def get_user_quality(user_id: str) -> str:
    """获取用户默认格式偏好；未设置或值非法时返回 flac。"""
    with _lock:
        raw = _read_all().get(str(user_id))
    q = str(raw or "").lower()
    return q if q in QUALITIES else DEFAULT_QUALITY


def set_user_quality(user_id: str, quality: str) -> None:
    """记录用户格式偏好（mp3 / flac）。"""
    quality = str(quality or "").lower()
    if quality not in QUALITIES:
        return
    with _lock:
        data = _read_all()
        data[str(user_id)] = quality
        _write_all(data)
    logger.info("[Netease] 用户格式偏好 → user=%s quality=%s", user_id, quality)


# ── 最近发送记录（内存） ──


@dataclass
class SentRecord:
    """一次网易云文件发送记录，用于回复换格式重发。"""

    user_id: str
    item_type: str  # song / program / album / playlist
    item_id: str
    title: str
    quality: str  # flac / mp3（实际请求的格式）
    message_ids: list[str] = field(default_factory=list)
    sent_at: float = 0.0


MAX_RECENT_PER_USER = 5
_recent: dict[str, list[SentRecord]] = {}


def record_send(
    user_id: str,
    *,
    item_type: str,
    item_id: str,
    title: str,
    quality: str,
    message_ids: Optional[list[str]] = None,
) -> None:
    """记录一次发送（每用户保留最近 MAX_RECENT_PER_USER 条）。"""
    ids = [str(m) for m in (message_ids or []) if str(m)]
    rec = SentRecord(
        user_id=str(user_id),
        item_type=item_type,
        item_id=str(item_id),
        title=title,
        quality=quality if quality in QUALITIES else DEFAULT_QUALITY,
        message_ids=ids,
        sent_at=time.time(),
    )
    with _lock:
        lst = _recent.setdefault(rec.user_id, [])
        lst.insert(0, rec)
        del lst[MAX_RECENT_PER_USER:]
    logger.info(
        "[Netease] 记录发送 → user=%s type=%s id=%s title=%s quality=%s message_ids=%d",
        rec.user_id, rec.item_type, rec.item_id, rec.title, rec.quality, len(ids),
    )


def find_recent_by_message_id(user_id: str, message_id: str) -> Optional[SentRecord]:
    """按 bot 发出的消息 ID 查找该用户最近的发送记录。"""
    mid = str(message_id)
    if not mid:
        return None
    with _lock:
        for rec in _recent.get(str(user_id), []):
            if mid in rec.message_ids:
                return rec
    return None
