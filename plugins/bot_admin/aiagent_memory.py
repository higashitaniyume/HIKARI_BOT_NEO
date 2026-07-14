"""AI Agent 记忆文件管理 —— 读取、列出、触发总结。"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from plugins.aiagent.client import post_chat_completion
from plugins.aiagent.config import get_config
from plugins.aiagent.memory import _collect_raw_entries, _replace_raw_with_summary, _SESSION_MARKER, _SUMMARIZE_SYSTEM_PROMPT, _summarizing_locks

logger = logging.getLogger("HikariBot.BotAdmin.AIAgentMemory")

_MEMORY_ROOT = Path("UserData/aiagent_memory")


def _resolve_absolute(root: Path) -> Path:
    if not root.is_absolute():
        root = Path.cwd() / root
    return root


def _safe_relative(file_path: str) -> Path | None:
    """将请求的路径安全解析为记忆文件的绝对路径。"""
    raw = Path(file_path).as_posix().removeprefix("/").strip()
    if not raw or ".." in raw.split("/"):
        return None
    root = _resolve_absolute(_MEMORY_ROOT).resolve()
    target = (root / raw).resolve()
    if root not in target.parents and target != root:
        return None
    if not target.is_file():
        return None
    if target.suffix.lower() != ".md":
        return None
    return target


def _list_memory_files() -> list[dict[str, Any]]:
    """递归列出所有记忆文件，分组展示。"""
    root = _resolve_absolute(_MEMORY_ROOT)
    if not root.is_dir():
        return []

    files: list[dict[str, Any]] = []

    # 群聊共享记忆
    groups_dir = root / "groups"
    if groups_dir.is_dir():
        for group_dir in sorted(groups_dir.iterdir()):
            if not group_dir.is_dir():
                continue
            mem_file = group_dir / "memory.md"
            if mem_file.is_file():
                files.append(_file_info("群聊共享", mem_file, root))
            # 群内个人记忆
            users_dir = group_dir / "users"
            if users_dir.is_dir():
                for user_dir in sorted(users_dir.iterdir()):
                    if not user_dir.is_dir():
                        continue
                    user_file = user_dir / "memory.md"
                    if user_file.is_file():
                        files.append(_file_info("群内个人", user_file, root))

    # 私聊个人记忆
    private_dir = root / "private"
    if private_dir.is_dir():
        for user_dir in sorted(private_dir.iterdir()):
            if not user_dir.is_dir():
                continue
            mem_file = user_dir / "memory.md"
            if mem_file.is_file():
                files.append(_file_info("私聊个人", mem_file, root))

    return files


def _file_info(kind: str, path: Path, root: Path) -> dict[str, Any]:
    """提取单个记忆文件的元信息。"""
    try:
        stat = path.stat()
        size = stat.st_size
        mtime = int(stat.st_mtime)
    except OSError:
        size = 0
        mtime = 0

    rel = path.resolve().relative_to(root.resolve()).as_posix()
    parts = rel.split("/")
    group_id = parts[1] if len(parts) >= 2 and parts[0] == "groups" else ""
    user_id = ""

    if kind == "群内个人":
        user_id = parts[3] if len(parts) >= 4 else ""
    elif kind == "私聊个人":
        user_id = parts[1] if len(parts) >= 2 else ""

    content = ""
    has_marker = False
    raw_entries_count = 0
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        has_marker = _SESSION_MARKER.strip() in content
        if has_marker:
            raw = _collect_raw_entries(path)
            if raw:
                raw_entries_count = raw.count("\n- ") + 1
    except OSError:
        pass

    return {
        "path": rel,
        "kind": kind,
        "group_id": group_id,
        "user_id": user_id,
        "size": size,
        "mtime": mtime,
        "has_marker": has_marker,
        "raw_entries": raw_entries_count,
    }


def _read_memory_file(file_path: str) -> dict[str, Any]:
    """读取指定记忆文件的内容。"""
    target = _safe_relative(file_path)
    if target is None:
        return {"error": f"文件不存在或路径无效: {file_path}"}
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
        return {"content": content, "path": file_path, "size": len(content)}
    except OSError as e:
        return {"error": f"读取失败: {e}"}


def aiagent_memory_state() -> dict[str, Any]:
    """状态接口：列出所有记忆文件。"""
    files = _list_memory_files()
    return {"files": files, "count": len(files), "root": _MEMORY_ROOT.as_posix()}


async def trigger_summarize(file_path: str) -> dict[str, Any]:
    """对指定记忆文件触发总结。"""
    target = _safe_relative(file_path)
    if target is None:
        return {"error": f"文件不存在或路径无效: {file_path}"}

    raw = _collect_raw_entries(target)
    if not raw:
        return {"error": "没有需要总结的原始记录", "path": file_path}
    if len(raw) < 200:
        return {"error": "原始记录过短（少于200字符），跳过总结", "path": file_path}

    if target in _summarizing_locks:
        return {"error": "该文件正在总结中，请稍后重试", "path": file_path}

    cfg = get_config()
    model_cfg = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
    api_key = str(model_cfg.get("api_key") or "")
    if not api_key or not str(model_cfg.get("model") or "").strip():
        return {"error": "AI Agent 模型未配置，无法调用总结", "path": file_path}

    _summarizing_locks.add(target)
    try:
        messages = [
            {"role": "system", "content": _SUMMARIZE_SYSTEM_PROMPT},
            {"role": "user", "content": f"请总结以下对话：\n\n{raw[:4000]}"},
        ]
        summary_msg = await post_chat_completion(cfg, messages, tools=[])
        summary = (summary_msg.get("content") or "").strip()
        if summary and "无重要信息" not in summary:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
            _replace_raw_with_summary(target, ts, summary)
            return {"result": f"✅ 已总结：\n{summary}", "path": file_path}
        return {"result": "对话内容较日常，跳过总结", "path": file_path}
    except Exception as e:
        logger.exception("[BotAdmin] 触发记忆总结失败: %s", e)
        return {"error": f"总结失败: {e}", "path": file_path}
    finally:
        _summarizing_locks.discard(target)
