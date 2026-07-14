"""AI Agent 记忆文件管理 —— 读取、列出、触发总结。"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from plugins.aiagent.client import post_chat_completion
from plugins.aiagent.config import get_config
from plugins.aiagent.memory import _SESSION_MARKER, _SUMMARIZE_SYSTEM_PROMPT, _summarizing_locks

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

    raw_entries_count = 0
    has_marker = False
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        marker_str = _SESSION_MARKER.strip()
        has_marker = marker_str in content
        # 有标记时取标记后内容，否则取 # AI Agent Memory 标题后的全部内容
        pos = content.rfind(marker_str) if has_marker else content.find("# AI Agent Memory")
        if pos >= 0:
            body = content[pos + len(marker_str if has_marker else "# AI Agent Memory"):].strip()
        else:
            body = content.strip()
        if body:
            raw_entries_count = body.count("\n- User(") + body.count("\n- Assistant:")
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


def _collect_body(path: Path) -> str | None:
    """获取记忆文件中有标记时取标记后内容，无标记时取标题后的全部内容。"""
    marker = _SESSION_MARKER.strip()
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    pos = content.rfind(marker)
    if pos >= 0:
        after = content[pos + len(marker):].strip()
        return after if after else None
    # 无标记 → 取 # AI Agent Memory 标题后的正文
    header = "# AI Agent Memory"
    hpos = content.find(header)
    if hpos >= 0:
        body = content[hpos + len(header):].strip()
        return body if body else None
    return content.strip() or None


def _write_summary(path: Path, timestamp: str, summary: str) -> None:
    """将总结写入记忆文件。有标记时替换标记后的原始记录，无标记时替换整个文件。"""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    marker = _SESSION_MARKER.strip()
    pos = content.rfind(marker)
    summary_block = f"## 会话摘要: {timestamp}\n{summary}\n{_SESSION_MARKER}"
    if pos >= 0:
        # 有标记 → 保留标记前的内容（摘要区），替换标记后的原始记录
        new_content = content[:pos].rstrip() + "\n\n" + summary_block
    else:
        # 无标记 → 整个文件都是原始记录，替换为 header + 摘要 + 标记
        new_content = f"# AI Agent Memory\n\n{summary_block}"
    try:
        path.write_text(new_content, encoding="utf-8")
    except OSError as e:
        logger.warning("[AIAgent] 写入记忆总结失败: %s -> %s", path, e)


async def trigger_summarize(file_path: str) -> dict[str, Any]:
    """对指定记忆文件触发总结。"""
    target = _safe_relative(file_path)
    if target is None:
        return {"error": f"文件不存在或路径无效: {file_path}"}

    raw = _collect_body(target)
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
            _write_summary(target, ts, summary)
            return {"result": f"✅ 已总结：\n{summary}", "path": file_path}
        return {"result": "对话内容较日常，跳过总结", "path": file_path}
    except Exception as e:
        logger.exception("[BotAdmin] 触发记忆总结失败: %s", e)
        return {"error": f"总结失败: {e}", "path": file_path}
    finally:
        _summarizing_locks.discard(target)
