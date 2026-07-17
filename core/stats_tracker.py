"""
统计信息模块。

记录每个群组/私聊的操作统计：
- 媒体解析（Pixiv / 聚合媒体 / Cobalt / YouTube / 网易云）
- 贴纸（发送 / TG 导入 / 拼图）
- 查询（Steam / AI 资讯 / 知乎 / RSS / Wiki / osu!）
- AI 对话、语音、TTS、JMComic 下载、点赞
- 会话元数据（首次活跃、最后活跃）

数据存储为 JSON 文件：UserData/stats/group_<id>.json / private_<id>.json
"""

from __future__ import annotations

import json
import logging
import atexit
import os
import threading
import time
from pathlib import Path
from typing import Any

from nonebot.adapters.onebot.v11 import Event, GroupMessageEvent, MessageEvent

from core.bot_messages import get_message as msg

logger = logging.getLogger("HikariBot.StatsTracker")

STATS_DIR = Path("UserData/stats")
STATS_DIR.mkdir(parents=True, exist_ok=True)
FLUSH_INTERVAL_SECONDS = 30.0

_stats_lock = threading.RLock()
_stats_cache: dict[Path, dict[str, Any]] = {}
_dirty_paths: set[Path] = set()
_last_flush_at = time.monotonic()

# 所有支持的统计键，按类别分组展示
STAT_KEY_CATEGORIES: dict[str, list[tuple[str, str]]] = {
    "media": [
        ("pixiv_parsed", "stats_label_pixiv_parsed"),
        ("media_parser_parsed", "stats_label_media_parser_parsed"),
        ("cobalt_parsed", "stats_label_cobalt_parsed"),
        ("youtube_downloaded", "stats_label_youtube_downloaded"),
        ("netease_parsed", "stats_label_netease_parsed"),
        ("tg_sticker_parsed", "stats_label_tg_sticker_parsed"),
    ],
    "sticker": [
        ("stickers_sent", "stats_label_stickers_sent"),
        ("collage_made", "stats_label_collage_made"),
    ],
    "query": [
        ("steam_queries", "stats_label_steam_queries"),
        ("ai_news_views", "stats_label_ai_news_views"),
        ("zhihu_hot_views", "stats_label_zhihu_hot_views"),
        ("rss_reads", "stats_label_rss_reads"),
        ("wiki_queries", "stats_label_wiki_queries"),
        ("osu_queries", "stats_label_osu_queries"),
    ],
    "interact": [
        ("ai_chat_sessions", "stats_label_ai_chat_sessions"),
        ("voice_triggers", "stats_label_voice_triggers"),
        ("tts_generated", "stats_label_tts_generated"),
        ("jmcomic_downloads", "stats_label_jmcomic_downloads"),
        ("profile_likes_given", "stats_label_profile_likes_given"),
    ],
}

# 扁平列表用于快速查找
ALL_STAT_KEYS = [key for cat in STAT_KEY_CATEGORIES.values() for key, _ in cat]


def _get_stats_path(event: MessageEvent) -> Path:
    """根据事件类型获取统计文件路径。"""
    if isinstance(event, GroupMessageEvent):
        return STATS_DIR / f"group_{event.group_id}.json"
    else:
        return STATS_DIR / f"private_{event.get_user_id()}.json"


def _read_stats(path: Path) -> dict[str, Any]:
    """读取统计 JSON，不存在则返回空字典。"""
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _write_stats(path: Path, data: dict[str, Any]) -> None:
    """写入统计 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def _get_cached_stats(path: Path) -> dict[str, Any]:
    with _stats_lock:
        cached = _stats_cache.get(path)
        if cached is None:
            cached = _read_stats(path)
            _stats_cache[path] = cached
        return cached


def flush_stats(force: bool = False) -> None:
    """把内存中的统计变更批量写回磁盘。"""
    global _last_flush_at
    now = time.monotonic()
    with _stats_lock:
        if not force and now - _last_flush_at < FLUSH_INTERVAL_SECONDS:
            return
        paths = list(_dirty_paths)
        if not paths:
            _last_flush_at = now
            return

        for path in paths:
            data = _stats_cache.get(path)
            if data is None:
                _dirty_paths.discard(path)
                continue
            try:
                _write_stats(path, data)
                _dirty_paths.discard(path)
            except Exception as e:
                logger.warning("[Stats] 写入统计失败: %s -> %s", path, e)
        _last_flush_at = now


def increment(event: MessageEvent, key: str, amount: int = 1) -> None:
    """增加指定统计项的值。

    Args:
        event: 消息事件
        key: 统计项名称（如 "stickers_sent", "pixiv_parsed"）
        amount: 增加值
    """
    path = _get_stats_path(event)
    now_ts = int(time.time())
    with _stats_lock:
        data = _get_cached_stats(path)
        data[key] = data.get(key, 0) + amount
        # 更新会话元数据
        _ensure_meta(data, now_ts)
        _dirty_paths.add(path)
    flush_stats(force=False)
    logger.debug("[Stats] %s %s += %d -> %d", _chat_label(event), key, amount, data[key])


def _ensure_meta(data: dict[str, Any], now_ts: int) -> None:
    """确保会话元数据字段存在并更新。"""
    meta: dict[str, Any] = data.get("_meta")
    if meta is None:
        meta = {}
        data["_meta"] = meta
    if "first_seen" not in meta:
        meta["first_seen"] = now_ts
    meta["last_active"] = now_ts
    meta["total_messages"] = meta.get("total_messages", 0) + 1


def mark_message(event: MessageEvent) -> None:
    """标记收到一条消息并更新会话活跃时间（每次收到消息时调用）。"""
    path = _get_stats_path(event)
    now_ts = int(time.time())
    with _stats_lock:
        data = _get_cached_stats(path)
        _ensure_meta(data, now_ts)
        _dirty_paths.add(path)
    flush_stats(force=False)


def get_stats(event: MessageEvent) -> dict[str, Any]:
    """获取当前会话的统计数据。"""
    with _stats_lock:
        return dict(_get_cached_stats(_get_stats_path(event)))


def get_meta(event: MessageEvent) -> dict[str, Any]:
    """获取当前会话的元数据。"""
    data = get_stats(event)
    return dict(data.get("_meta", {}))


def session_count(event: MessageEvent) -> int:
    """获取当前会话的总消息数。"""
    meta = get_meta(event)
    return meta.get("total_messages", 0)


# ── 全局聚合 ──


def get_global_stats() -> dict[str, Any]:
    """读取所有会话文件，聚合全局统计数据。

    Returns:
        包含 totals（各统计键合计）和 sessions（会话数量）的字典。
    """
    from os import listdir

    totals: dict[str, int] = {}
    group_count = 0
    private_count = 0
    earliest_seen: int | None = None
    latest_active: int | None = None

    for fname in listdir(str(STATS_DIR)):
        fpath = STATS_DIR / fname
        if not fpath.is_file() or not fname.endswith(".json"):
            continue
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue

        for key in ALL_STAT_KEYS:
            val = data.get(key, 0)
            if isinstance(val, (int, float)):
                totals[key] = totals.get(key, 0) + int(val)

        meta = data.get("_meta")
        if isinstance(meta, dict):
            fs = meta.get("first_seen")
            if isinstance(fs, (int, float)):
                if earliest_seen is None or int(fs) < earliest_seen:
                    earliest_seen = int(fs)
            la = meta.get("last_active")
            if isinstance(la, (int, float)):
                if latest_active is None or int(la) > latest_active:
                    latest_active = int(la)

        if fname.startswith("group_"):
            group_count += 1
        elif fname.startswith("private_"):
            private_count += 1

    return {
        "totals": totals,
        "sessions": {
            "groups": group_count,
            "private": private_count,
            "total": group_count + private_count,
        },
        "earliest_seen": earliest_seen,
        "latest_active": latest_active,
    }


def format_global_stats(global_data: dict[str, Any]) -> str:
    """格式化全局统计为可发送文本。"""
    totals = global_data.get("totals", {})
    sessions = global_data.get("sessions", {})
    lines: list[str] = [msg("stats.global_header"), ""]

    if sessions.get("total", 0) > 0:
        lines.append(
            msg(
                "stats.global_sessions",
                groups=sessions.get("groups", 0),
                private=sessions.get("private", 0),
            )
        )
        lines.append("")

    has_any = False
    for category_key, category_label_attr in [
        ("media", "stats.global_category_media"),
        ("sticker", "stats.global_category_sticker"),
        ("query", "stats.global_category_query"),
        ("interact", "stats.global_category_interact"),
    ]:
        items = STAT_KEY_CATEGORIES.get(category_key, [])
        cat_lines: list[str] = []
        for key, label_key in items:
            val = totals.get(key, 0)
            if val > 0:
                cat_lines.append(msg("stats.global_line", label=msg(label_key), count=val))
        if cat_lines:
            has_any = True
            lines.append(msg(category_label_attr))
            lines.extend(cat_lines)
            lines.append("")

    if not has_any:
        lines.append(msg("common.none_stats"))
    else:
        earliest = global_data.get("earliest_seen")
        if earliest:
            import datetime
            dt = datetime.datetime.fromtimestamp(earliest, tz=datetime.timezone(datetime.timedelta(hours=8)))
            lines.append(msg("stats.global_first_session", date=dt.strftime("%Y-%m-%d %H:%M")))

    return "\n".join(lines).rstrip("\n")


def format_stats(event: MessageEvent) -> str:
    """格式化当前会话统计信息为可发送的文本。"""
    data = get_stats(event)
    if not data:
        return msg("common.none_stats")

    lines: list[str] = [msg("common.stats_header"), ""]

    # 会话元数据
    meta = data.get("_meta")
    if isinstance(meta, dict):
        total_msgs = meta.get("total_messages", 0)
        if total_msgs:
            lines.append(msg("stats.session_messages", count=total_msgs))

    has_any = False
    for category_key, category_label_attr in [
        ("media", "stats.category_media"),
        ("sticker", "stats.category_sticker"),
        ("query", "stats.category_query"),
        ("interact", "stats.category_interact"),
    ]:
        items = STAT_KEY_CATEGORIES.get(category_key, [])
        cat_lines: list[str] = []
        for key, label_key in items:
            val = data.get(key, 0)
            if val > 0:
                cat_lines.append(msg("common.stats_line", label=msg(label_key), count=val))
        if cat_lines:
            has_any = True
            lines.append("")
            lines.append(msg(category_label_attr))
            lines.extend(cat_lines)

    if not has_any:
        return msg("common.none_stats")

    return "\n".join(lines)


def _chat_label(event: MessageEvent) -> str:
    """生成会话标签用于日志。"""
    if isinstance(event, GroupMessageEvent):
        return f"[群:{event.group_id}]"
    return f"[私聊:{event.get_user_id()}]"


atexit.register(lambda: flush_stats(force=True))
