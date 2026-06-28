from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent

from .utils import safe_id, safe_int

logger = logging.getLogger("HikariBot.AIAgent.Memory")

_histories: dict[str, list[dict[str, str]]] = {}


def session_key(event: MessageEvent) -> str:
    if isinstance(event, GroupMessageEvent):
        return f"group:{event.group_id}"
    return f"private:{event.get_user_id()}"


def trim_history(history: list[dict[str, str]], max_messages: Any) -> list[dict[str, str]]:
    limit = safe_int(max_messages, 10, minimum=0, maximum=40)
    if limit <= 0:
        return []
    return history[-limit:]


def get_history(session: str, max_messages: Any) -> list[dict[str, str]]:
    return trim_history(_histories.get(session, []), max_messages)


def remember(session: str, user_text: str, assistant_text: str, cfg: dict[str, Any]) -> None:
    chat_cfg = cfg.get("chat") if isinstance(cfg.get("chat"), dict) else {}
    history = _histories.setdefault(session, [])
    history.extend([
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": assistant_text},
    ])
    _histories[session] = trim_history(history, chat_cfg.get("max_history_messages"))


def clear_session(session: str) -> None:
    _histories.pop(session, None)


def _memory_root(cfg: dict[str, Any]) -> Path:
    memory_cfg = cfg.get("memory") if isinstance(cfg.get("memory"), dict) else {}
    root = Path(str(memory_cfg.get("root") or "UserData/aiagent_memory"))
    if not root.is_absolute():
        root = Path.cwd() / root
    return root


def memory_paths(event: MessageEvent, cfg: dict[str, Any]) -> list[tuple[str, Path]]:
    memory_cfg = cfg.get("memory") if isinstance(cfg.get("memory"), dict) else {}
    if not memory_cfg.get("enabled", True):
        return []

    root = _memory_root(cfg)
    user_id = safe_id(event.get_user_id())
    if isinstance(event, GroupMessageEvent):
        group_id = safe_id(event.group_id)
        return [
            ("群聊共享记忆", root / "groups" / group_id / "memory.md"),
            ("群内个人记忆", root / "groups" / group_id / "users" / user_id / "memory.md"),
        ]
    return [("私聊个人记忆", root / "private" / user_id / "memory.md")]


def read_memory_context(event: MessageEvent, cfg: dict[str, Any]) -> str:
    memory_cfg = cfg.get("memory") if isinstance(cfg.get("memory"), dict) else {}
    max_chars = safe_int(memory_cfg.get("max_read_chars_per_file"), 8000, minimum=1000, maximum=80000)
    blocks: list[str] = []
    for label, path in memory_paths(event, cfg):
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if not content:
            continue
        blocks.append(f"## {label}\n{content[-max_chars:]}")
    if not blocks:
        return ""
    return "以下是持久化记忆。请把它作为背景参考；不要主动复述文件内容。\n\n" + "\n\n".join(blocks)


def _trim_memory_file(path: Path, max_chars: int) -> None:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    if len(content) <= max_chars:
        return
    trimmed = content[-max_chars:].lstrip()
    path.write_text(f"# AI Agent Memory\n\n{trimmed}", encoding="utf-8")


def append_memory(event: MessageEvent, cfg: dict[str, Any], user_text: str, assistant_text: str) -> None:
    memory_cfg = cfg.get("memory") if isinstance(cfg.get("memory"), dict) else {}
    if not memory_cfg.get("enabled", True):
        return

    max_file_chars = safe_int(memory_cfg.get("max_file_chars"), 60000, minimum=5000, maximum=500000)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = (
        f"\n\n## {now}\n"
        f"- User({event.get_user_id()}): {user_text}\n"
        f"- Assistant: {assistant_text}\n"
    )
    for _, path in memory_paths(event, cfg):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("# AI Agent Memory\n", encoding="utf-8")
            with path.open("a", encoding="utf-8") as f:
                f.write(entry)
            _trim_memory_file(path, max_file_chars)
        except OSError as e:
            logger.warning("[AIAgent] 写入 memory 失败: %s -> %s", path, e)


def clear_memory(event: MessageEvent, cfg: dict[str, Any]) -> None:
    for _, path in memory_paths(event, cfg):
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            logger.warning("[AIAgent] 清空 memory 失败: %s -> %s", path, e)
