"""AI Agent 群消息本地记录（chatlog）。

NapCat 无数据库、消息走 LRU 缓存（约 5000 条即被清理），所以「总结某人之前说了什么」
这类需求必须靠机器人自己把群消息记下来。这里：

- 只记录**纯文本**消息（纯图片/语音/表情等没有文本的消息直接跳过）；
- 按 `群号/日期` 分文件存 JSONL，一行一条：`{"t": 时间戳, "u": QQ, "n": 显示名, "c": 文本}`；
- 带保留期（`retention_days`）与总量上限（`max_total_mb`），超期/超量从最旧的删；
- 默认记录所有群，可用 `groups` 白名单收窄；
- 写入失败只记日志，绝不影响消息流水线。
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent

from .config import get_config_for_event
from .utils import (
    format_timestamp,
    message_has_text,
    message_plain_text,
    safe_bool,
    safe_id,
    safe_int,
)

logger = logging.getLogger("HikariBot.AIAgent.ChatLog")

# 记录根目录（测试会替换它）
CHATLOG_ROOT = Path("UserData/aiagent_chatlog")
# 单条记录最多保存的字符数，避免一条超长消息把文件撑大
MAX_RECORD_CHARS = 1000
# 清理任务最小间隔（秒）：不必每条消息都扫一遍磁盘
_PRUNE_INTERVAL_SECONDS = 3600
# 读取单条发言最多返回的字符数
MAX_READ_TEXT_CHARS = 500
# 读取单个记录文件时最多读入的字节数：超大文件只读末尾一段
# （读取顺序是从新到旧，末尾即最新，足够覆盖 limit 条以内的查询）
MAX_READ_BYTES = 512 * 1024

_last_prune_at = 0.0

# 被动记录：优先级高于 AI 回复（99），且 block=False，不影响任何其它插件
chatlog_recorder = on_message(priority=80, block=False)


# ── 配置 ──────────────────────────────────────────────────────────────────


def config(cfg: dict[str, Any]) -> dict[str, Any]:
    section = cfg.get("chatlog") if isinstance(cfg, dict) else None
    return section if isinstance(section, dict) else {}


def enabled(cfg: dict[str, Any]) -> bool:
    return safe_bool(config(cfg).get("enabled"), True)


def record_bot(cfg: dict[str, Any]) -> bool:
    """是否把机器人自己的发言也记下来（默认否）。"""
    return safe_bool(config(cfg).get("record_bot"), False)


def retention_days(cfg: dict[str, Any]) -> int:
    return safe_int(config(cfg).get("retention_days"), 7, minimum=1, maximum=365)


def max_total_mb(cfg: dict[str, Any]) -> int:
    return safe_int(config(cfg).get("max_total_mb"), 200, minimum=1, maximum=20000)


def allowed_group(cfg: dict[str, Any], group_id: Any) -> bool:
    """群白名单；为空表示所有群都记录。"""
    groups = config(cfg).get("groups")
    if not isinstance(groups, list):
        return True
    allowed = {str(item).strip() for item in groups if str(item).strip()}
    if not allowed:
        return True
    return str(group_id) in allowed


# ── 路径 ──────────────────────────────────────────────────────────────────


def _group_dir(group_id: Any) -> Path:
    return CHATLOG_ROOT / safe_id(group_id)


def _day_path(group_id: Any, day: date) -> Path:
    return _group_dir(group_id) / f"{day.isoformat()}.jsonl"


def _parse_day(path: Path) -> date | None:
    try:
        return date.fromisoformat(path.stem)
    except ValueError:
        return None


def _all_day_files() -> list[Path]:
    if not CHATLOG_ROOT.is_dir():
        return []
    files: list[tuple[date, str, Path]] = []
    for path in CHATLOG_ROOT.glob("*/*.jsonl"):
        day = _parse_day(path)
        if day is None:
            continue
        files.append((day, str(path), path))
    files.sort()
    return [item[2] for item in files]


def _display_name(event: GroupMessageEvent) -> str:
    sender = getattr(event, "sender", None)
    card = str(getattr(sender, "card", "") or "").strip()
    nickname = str(getattr(sender, "nickname", "") or "").strip()
    return card or nickname or str(event.get_user_id())


# ── 写入 ──────────────────────────────────────────────────────────────────


def record_message(event: GroupMessageEvent, cfg: dict[str, Any]) -> bool:
    """把一条群消息写入本地记录；未启用/该群未授权/没有文本时不写。"""
    if not enabled(cfg) or not allowed_group(cfg, event.group_id):
        return False

    user_id = str(event.get_user_id())
    if not record_bot(cfg) and user_id == str(getattr(event, "self_id", "")):
        return False

    message = event.get_message()
    if not message_has_text(message):
        return False
    text = message_plain_text(message, max_chars=MAX_RECORD_CHARS)
    if not text:
        return False

    entry = {
        "t": int(getattr(event, "time", 0) or time.time()),
        "u": user_id,
        "n": _display_name(event),
        "c": text,
    }
    path = _day_path(event.group_id, date.today())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning("[AIAgent] 写入聊天记录失败: %s -> %s", path, e)
        return False

    prune(cfg)
    return True


# ── 清理 ──────────────────────────────────────────────────────────────────


def _unlink(path: Path) -> bool:
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError as e:
        logger.warning("[AIAgent] 清理聊天记录失败: %s -> %s", path, e)
        return False


def _cleanup_empty_dirs() -> None:
    if not CHATLOG_ROOT.is_dir():
        return
    for group_dir in CHATLOG_ROOT.iterdir():
        if not group_dir.is_dir():
            continue
        try:
            if not any(group_dir.iterdir()):
                group_dir.rmdir()
        except OSError:
            continue


def prune(cfg: dict[str, Any], *, force: bool = False, now: float | None = None) -> int:
    """按保留期与总量上限清理记录文件，返回删除的文件数。

    默认每小时最多执行一次（`force=True` 可强制），避免每条消息都扫盘。
    """
    global _last_prune_at
    current = time.monotonic() if now is None else float(now)
    if not force and current - _last_prune_at < _PRUNE_INTERVAL_SECONDS:
        return 0
    _last_prune_at = current

    files = _all_day_files()
    if not files:
        return 0

    removed = 0
    cutoff = date.today() - timedelta(days=retention_days(cfg))
    for path in files:
        day = _parse_day(path)
        if day is not None and day < cutoff and _unlink(path):
            removed += 1

    # 总量上限：从最旧的开始删
    limit_bytes = max_total_mb(cfg) * 1024 * 1024
    sizes: list[tuple[Path, int]] = []
    total = 0
    for path in _all_day_files():
        try:
            size = path.stat().st_size
        except OSError:
            continue
        sizes.append((path, size))
        total += size
    for path, size in sizes:
        if total <= limit_bytes:
            break
        if _unlink(path):
            removed += 1
            total -= size

    _cleanup_empty_dirs()
    if removed:
        logger.info("[AIAgent] 聊天记录清理完成，删除 %d 个文件", removed)
    return removed


# ── 读取 ──────────────────────────────────────────────────────────────────


def _read_lines(path: Path) -> list[str]:
    """读取记录文件的行（从新到旧遍历由调用方负责）。

    文件超过 `MAX_READ_BYTES` 时只读末尾一段（末尾是最新的记录），避免为一次查询
    把整个大文件读进内存。
    """
    try:
        if not path.is_file():
            return []
        size = path.stat().st_size
        if size <= MAX_READ_BYTES:
            return path.read_text(encoding="utf-8", errors="replace").splitlines()
        with path.open("rb") as f:
            f.seek(size - MAX_READ_BYTES)
            chunk = f.read()
        text = chunk.decode("utf-8", errors="replace")
        # 第一行可能被截断在半路，丢掉它
        _, _, rest = text.partition("\n")
        return rest.splitlines()
    except OSError as e:
        logger.warning("[AIAgent] 读取聊天记录失败: %s -> %s", path, e)
        return []


def read_user_messages(
    cfg: dict[str, Any],
    group_id: Any,
    user_id: Any,
    *,
    limit: int,
    keyword: str = "",
) -> list[dict[str, Any]]:
    """返回某成员在**本群**的本地发言，按时间正序，最多 limit 条（保留最新的）。

    记录功能关闭时返回空列表：关闭即视为不提供该能力，但已有文件不会被删除。
    """
    if limit <= 0 or not enabled(cfg):
        return []

    group_dir = _group_dir(group_id)
    if not group_dir.is_dir():
        return []

    target = str(user_id)
    needle = keyword.casefold()
    collected: list[dict[str, Any]] = []

    for path in sorted(group_dir.glob("*.jsonl"), reverse=True):
        for line in reversed(_read_lines(path)):
            raw = line.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict) or str(entry.get("u")) != target:
                continue
            text = str(entry.get("c") or "").strip()
            if not text:
                continue
            if needle and needle not in text.casefold():
                continue
            if len(text) > MAX_READ_TEXT_CHARS:
                text = text[:MAX_READ_TEXT_CHARS]
            timestamp = entry.get("t")
            collected.append(
                {
                    "ts": int(timestamp) if isinstance(timestamp, int) else 0,
                    "time": format_timestamp(timestamp),
                    "text": text,
                }
            )
            if len(collected) >= limit:
                break
        if len(collected) >= limit:
            break

    collected.reverse()
    return collected


# ── 被动记录入口 ──────────────────────────────────────────────────────────


@chatlog_recorder.handle()
async def _handle_chatlog_record(event: MessageEvent) -> None:
    """记录一条群消息；任何异常都不能影响消息流水线。

    `on_message` 也会收到私聊事件，所以这里用基类标注再自行判断群聊
    （与 sticker_collector 的被动收集一致）。
    """
    if not isinstance(event, GroupMessageEvent):
        return
    try:
        cfg = get_config_for_event(event)
        record_message(event, cfg)
    except Exception as e:  # noqa: BLE001 - 记录失败不能影响其它插件
        logger.debug("[AIAgent] 跳过聊天记录: %s", e)
