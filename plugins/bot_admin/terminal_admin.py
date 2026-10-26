"""终端（remote_shell）后台数据：用户清单、调用历史、运行输出。

数据来源都在 bot 本地，不访问远端机器：
- `UserData/remote_shell/state.json`：已开通的群/私聊账号与 cwd
- `UserData/remote_shell/audit.jsonl`：调用审计（jsonl，追加写）
- `UserData/remote_shell/logs/<session>/*.log`：单次命令的完整输出
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from plugins.remote_shell.config import load_config as load_remote_shell_config
from plugins.remote_shell.store import StateStore

logger = logging.getLogger("HikariBot.BotAdmin.Terminal")

_ACTIONS = {"exec", "root_exec", "write", "edit", "provision", "fetch", "job_start", "job_kill"}
_SESSION_RE = re.compile(r"^[gp]\d{1,20}$")
_LOG_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}\.log$")
_RECORD_TAIL_LINES = 1200
_MAX_LOG_BYTES = 512 * 1024
_total_cache: dict[str, tuple[tuple[int, int], int]] = {}


def _cfg(cfg: dict[str, Any] | None) -> dict[str, Any]:
    return cfg if isinstance(cfg, dict) else load_remote_shell_config()


def _state_path(cfg: dict[str, Any]) -> Path:
    return Path(str(cfg.get("state_path") or "UserData/remote_shell/state.json"))


def _audit_path(cfg: dict[str, Any]) -> Path:
    audit = cfg.get("audit") if isinstance(cfg.get("audit"), dict) else {}
    return Path(str(audit.get("path") or "UserData/remote_shell/audit.jsonl"))


def _logs_root(cfg: dict[str, Any]) -> Path:
    logs = cfg.get("logs") if isinstance(cfg.get("logs"), dict) else {}
    return Path(str(logs.get("root") or "UserData/remote_shell/logs"))


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def _tail_lines(path: Path, limit: int, max_bytes: int = 1_500_000) -> list[str]:
    if limit <= 0 or not path.is_file():
        return []
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(0, size - max_bytes)
            handle.seek(start)
            blob = handle.read()
    except OSError as e:
        logger.warning("读取终端审计失败 %s: %s", path, e)
        return []

    lines = blob.decode("utf-8", errors="replace").splitlines()
    if start > 0 and lines:
        # 第一行大概率是截断的，丢掉。
        lines = lines[1:]
    return lines[-limit:]


def _count_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        stat = path.stat()
    except OSError:
        return 0

    key = (stat.st_mtime_ns, stat.st_size)
    cached = _total_cache.get(str(path))
    if cached and cached[0] == key:
        return cached[1]

    total = 0
    try:
        with open(path, "rb") as handle:
            for _ in handle:
                total += 1
    except OSError:
        return 0
    _total_cache[str(path)] = (key, total)
    return total


def _session_kind(key: str) -> str:
    return "group" if key.startswith("g") else "private" if key.startswith("p") else "unknown"


def _session_id(key: str) -> str:
    return key[1:] if key[:1] in {"g", "p"} else key


def _session_rows(state: dict[str, Any]) -> list[dict[str, Any]]:
    today = _today()
    groups = state.get("groups") if isinstance(state.get("groups"), dict) else {}
    now = int(time.time())
    rows: list[dict[str, Any]] = []

    for key, entry in groups.items():
        if not isinstance(entry, dict):
            continue
        daily = entry.get("daily") if isinstance(entry.get("daily"), dict) else {}
        today_calls = int(daily.get("count") or 0) if daily.get("date") == today else 0
        last_used = int(entry.get("last_used") or 0)
        rows.append(
            {
                "key": str(key),
                "kind": _session_kind(str(key)),
                "id": _session_id(str(key)),
                "user": str(entry.get("user") or ""),
                "home": str(entry.get("home") or ""),
                "cwd": str(entry.get("cwd") or ""),
                "created_at": int(entry.get("created_at") or 0),
                "last_used": last_used,
                "today_calls": today_calls,
                "active_24h": bool(last_used and now - last_used <= 86400),
                "provisioned": bool(entry.get("provisioned")),
            }
        )

    rows.sort(key=lambda item: (item["last_used"], item["created_at"]), reverse=True)
    return rows


def _normalize_record(raw: dict[str, Any]) -> dict[str, Any]:
    session = str(raw.get("session") or "")
    log_path = str(raw.get("log") or "")
    return {
        "ts": str(raw.get("ts") or ""),
        "session": session,
        "kind": _session_kind(session),
        "id": str(raw.get("group_id") or _session_id(session)),
        "action": str(raw.get("action") or ""),
        "user": str(raw.get("user") or ""),
        "command": str(raw.get("command") or ""),
        "path": str(raw.get("path") or ""),
        "exit_code": raw.get("exit_code"),
        "duration_ms": raw.get("duration_ms"),
        "bytes": raw.get("bytes"),
        "replacements": raw.get("replacements"),
        "truncated": bool(raw.get("truncated")),
        "log_name": Path(log_path).name if log_path else "",
    }


def _matches(record: dict[str, Any], needle: str) -> bool:
    if not needle:
        return True
    haystack = " ".join(
        str(record.get(key) or "")
        for key in ("session", "id", "user", "command", "path", "ts")
    )
    return needle in haystack.casefold()


def terminal_overview(
    *,
    limit: int = 100,
    query: str = "",
    action: str = "",
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """终端页数据：账号清单 + 调用历史 + 汇总指标。"""

    resolved = _cfg(cfg)
    limit = max(1, min(int(limit or 100), 500))
    query = str(query or "").strip()[:120]
    action = str(action or "").strip().lower()
    if action not in _ACTIONS:
        action = ""

    state = StateStore(_state_path(resolved)).load()
    sessions = _session_rows(state)

    records: list[dict[str, Any]] = []
    for line in reversed(_tail_lines(_audit_path(resolved), _RECORD_TAIL_LINES)):
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        item = _normalize_record(raw)
        if action and item["action"] != action:
            continue
        if not _matches(item, query.casefold()):
            continue
        records.append(item)
        if len(records) >= limit:
            break

    users_cfg = resolved.get("users") if isinstance(resolved.get("users"), dict) else {}
    ssh_cfg = resolved.get("ssh") if isinstance(resolved.get("ssh"), dict) else {}
    groups_cfg = resolved.get("groups") if isinstance(resolved.get("groups"), dict) else {}
    root_cfg = resolved.get("root") if isinstance(resolved.get("root"), dict) else {}

    stats = {
        "users": len(sessions),
        "groups": sum(1 for row in sessions if row["kind"] == "group"),
        "privates": sum(1 for row in sessions if row["kind"] == "private"),
        "today_calls": sum(row["today_calls"] for row in sessions),
        "total_calls": _count_lines(_audit_path(resolved)),
        "active_24h": sum(1 for row in sessions if row["active_24h"]),
        "enabled": bool(resolved.get("enabled")),
        "private_enabled": bool(users_cfg.get("allow_private")),
        "root_enabled": bool(root_cfg.get("enabled")),
        "root_in_groups": bool(root_cfg.get("allow_in_groups")),
        "blocked_groups": len(groups_cfg.get("blocked") or []),
        "target": f"{ssh_cfg.get('host') or '-'}:{ssh_cfg.get('port') or '-'}",
        "history_limit": limit,
    }

    return {
        "stats": stats,
        "sessions": sessions,
        "records": records,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
    }


def read_terminal_log(
    session: str,
    name: str,
    *,
    cfg: dict[str, Any] | None = None,
    max_bytes: int = _MAX_LOG_BYTES,
) -> dict[str, Any]:
    """按会话 + 文件名读取单次命令的完整输出（限制在日志目录内）。"""

    resolved = _cfg(cfg)
    session = str(session or "").strip()
    name = str(name or "").strip()
    if not _SESSION_RE.fullmatch(session):
        raise ValueError("会话标识不合法")
    if not _LOG_NAME_RE.fullmatch(name):
        raise ValueError("日志文件名不合法")

    root = _logs_root(resolved).resolve()
    path = (root / session / name).resolve()
    if root != path.parent and root not in path.parents:
        raise ValueError("日志路径不合法")
    if not path.is_file():
        raise FileNotFoundError("日志不存在或已被清理")

    size = path.stat().st_size
    with open(path, "rb") as handle:
        if size > max_bytes:
            handle.seek(size - max_bytes)
        blob = handle.read(max_bytes)

    return {
        "session": session,
        "name": name,
        "size": size,
        "truncated": size > max_bytes,
        "content": blob.decode("utf-8", errors="replace"),
    }
