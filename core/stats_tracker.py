"""
统计信息模块。

记录每个群组/私聊的操作统计：
- 表情包发送次数
- Pixiv 解析次数
- Instagram/Facebook 解析次数
- 拼图次数

数据存储为 JSON 文件：UserData/stats/group_<id>.json / private_<id>.json
"""

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
_stats_cache: dict[Path, dict[str, int]] = {}
_dirty_paths: set[Path] = set()
_last_flush_at = time.monotonic()


def _get_stats_path(event: MessageEvent) -> Path:
    """根据事件类型获取统计文件路径。"""
    if isinstance(event, GroupMessageEvent):
        return STATS_DIR / f"group_{event.group_id}.json"
    else:
        return STATS_DIR / f"private_{event.get_user_id()}.json"


def _read_stats(path: Path) -> dict[str, int]:
    """读取统计 JSON，不存在则返回空字典。"""
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _write_stats(path: Path, data: dict[str, int]) -> None:
    """写入统计 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def _get_cached_stats(path: Path) -> dict[str, int]:
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
    with _stats_lock:
        data = _get_cached_stats(path)
        data[key] = data.get(key, 0) + amount
        _dirty_paths.add(path)
        current_value = data[key]
    flush_stats(force=False)
    logger.debug(f"[Stats] {_chat_label(event)} {key} += {amount} → {current_value}")


def get_stats(event: MessageEvent) -> dict[str, int]:
    """获取当前会话的统计数据。"""
    with _stats_lock:
        return dict(_get_cached_stats(_get_stats_path(event)))


def format_stats(event: MessageEvent) -> str:
    """格式化统计信息为可发送的文本。"""
    data = get_stats(event)
    if not data:
        return msg("common.none_stats")

    labels = {
        "stickers_sent": "发送表情包",
        "pixiv_parsed": "Pixiv 解析",
        "cobalt_parsed": "媒体链接解析",
        "collage_made": "拼图",
    }

    lines = ["📊 统计信息：", ""]
    for key, label in labels.items():
        val = data.get(key, 0)
        if val > 0:
            lines.append(f"  {label}: {val} 次")
    return "\n".join(lines)


def _chat_label(event: MessageEvent) -> str:
    """生成会话标签用于日志。"""
    if isinstance(event, GroupMessageEvent):
        return f"[群:{event.group_id}]"
    return f"[私聊:{event.get_user_id()}]"


atexit.register(lambda: flush_stats(force=True))
