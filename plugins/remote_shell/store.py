"""remote_shell 的持久化：群会话状态、审计日志、命令输出日志。"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger("HikariBot.RemoteShell.Store")

STATE_VERSION = 1


def _empty_state() -> dict[str, Any]:
    return {"version": STATE_VERSION, "groups": {}}


class StateStore:
    """`UserData/remote_shell/state.json` 的原子读写。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return _empty_state()
        except OSError as e:
            logger.warning("读取终端状态失败 %s: %s", self.path, e)
            return _empty_state()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("终端状态文件损坏，已按空状态处理: %s", self.path)
            return _empty_state()

        if not isinstance(data, dict):
            return _empty_state()
        groups = data.get("groups")
        if not isinstance(groups, dict):
            data["groups"] = {}
        data["version"] = STATE_VERSION
        return data

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{self.path.name}.{uuid.uuid4().hex}.tmp")
        payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
        try:
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self.path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass


def get_group(data: dict[str, Any], group_key: str) -> dict[str, Any] | None:
    groups = data.get("groups")
    if not isinstance(groups, dict):
        return None
    entry = groups.get(group_key)
    return entry if isinstance(entry, dict) else None


def set_group(data: dict[str, Any], group_key: str, entry: dict[str, Any]) -> None:
    groups = data.setdefault("groups", {})
    if not isinstance(groups, dict):
        groups = {}
        data["groups"] = groups
    groups[group_key] = entry


def append_audit(path: str | Path, record: dict[str, Any], max_bytes: int = 8 * 1024 * 1024) -> None:
    """追加一行审计 JSON；文件过大时滚动成 .1。"""

    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if file_path.exists() and file_path.stat().st_size > max_bytes:
            backup = file_path.with_suffix(file_path.suffix + ".1")
            os.replace(file_path, backup)
        line = json.dumps(record, ensure_ascii=False)
        with open(file_path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError as e:
        logger.warning("写入终端审计失败 %s: %s", file_path, e)


def write_command_log(
    root: str | Path,
    group_key: str,
    *,
    command: str,
    output: str,
    exit_code: int,
    keep: int = 100,
) -> str:
    """把完整输出落盘，返回相对工作目录的路径（供人排查，模型拿不到）。"""

    base = Path(root) / group_key
    base.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    file_path = base / f"{stamp}-{uuid.uuid4().hex[:6]}.log"
    body = (
        f"# time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}\n"
        f"# group: {group_key}\n"
        f"# exit: {exit_code}\n"
        f"# command:\n{command}\n"
        f"{'-' * 60}\n"
        f"{output}\n"
    )
    try:
        file_path.write_text(body, encoding="utf-8")
        _prune(base, keep)
    except OSError as e:
        logger.warning("写入命令日志失败 %s: %s", file_path, e)
        return ""
    return file_path.as_posix()


def _prune(directory: Path, keep: int) -> None:
    if keep <= 0:
        return
    try:
        files = sorted(
            (item for item in directory.iterdir() if item.is_file()),
            key=lambda item: item.stat().st_mtime,
        )
    except OSError:
        return
    for stale in files[:-keep]:
        try:
            stale.unlink()
        except OSError:
            pass
