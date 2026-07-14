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
                path.write_text(f"# AI Agent Memory\n\n{_SESSION_MARKER}", encoding="utf-8")
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


# ── 会话摘要 / 自动总结 ──────────────────────────────────────────────

from .client import post_chat_completion

_SESSION_MARKER: str = "\n<!-- current session -->\n"
_last_activity: dict[str, datetime] = {}
_summarizing_locks: set[Path] = set()

_SUMMARIZE_SYSTEM_PROMPT = (
    "你是一个聊天记忆总结助手。请将以下对话记录总结为简洁的要点。\n\n"
    "要求：\n"
    "- 只提取客观事实：用户说过什么、偏好什么、做出过什么决定\n"
    "- 不要添加原始对话中没有的信息\n"
    "- 使用中文，每条一行，用 - 开头\n"
    "- 保持简洁，不超过300字\n"
    "- 如果对话内容很日常（打招呼、寒暄等），只回复「无重要信息」"
)


def mark_activity(session: str) -> None:
    """记录当前会话的最后活动时间。"""
    _last_activity[session] = datetime.now()


def should_summarize(session: str, gap_minutes: int = 10) -> bool:
    """检查距离上次活动是否已超过 gap_minutes，且上次活动后尚未总结过。

    只读不写状态，调用方需自行调用 mark_activity()。
    """
    last = _last_activity.get(session)
    if last is None:
        return False
    return (datetime.now() - last).total_seconds() / 60 >= gap_minutes


def _collect_raw_entries(path: Path) -> str | None:
    """读取文件中会话标记之后的原始记录。"""
    if not path.is_file():
        return None
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    marker = _SESSION_MARKER.strip()
    pos = content.rfind(marker)
    if pos < 0:
        return None
    after = content[pos + len(marker):].strip()
    return after if after else None


def _replace_raw_with_summary(path: Path, timestamp: str, summary: str) -> None:
    """将会话标记后的原始记录替换为摘要区块，同时保留标记供后续追加。"""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    marker = _SESSION_MARKER.strip()
    pos = content.rfind(marker)
    summary_block = f"\n\n## 会话摘要: {timestamp}\n{summary}\n{_SESSION_MARKER}"
    new_content = (content[:pos] + summary_block) if pos >= 0 else content.rstrip() + "\n\n" + summary_block
    try:
        path.write_text(new_content, encoding="utf-8")
    except OSError as e:
        logger.warning("[AIAgent] 写入记忆总结失败: %s -> %s", path, e)


async def summarize_session_memory(
    cfg: dict[str, Any],
    event: MessageEvent,
    force: bool = False,
) -> str:
    """将上一轮会话的原始对话总结为要点写入记忆文件。

    返回人类可读的结果描述。
    自动触发时 force=False，需满足空闲间隔条件；手动命令时 force=True 跳过间隔检查。
    """
    session = session_key(event)
    if not force and not should_summarize(session):
        return ""

    results: list[str] = []
    for _label, path in memory_paths(event, cfg):
        if path in _summarizing_locks:
            results.append(f"【{_label}】正在总结中，跳过")
            continue
        raw = _collect_raw_entries(path)
        if not raw or len(raw) < 200:
            continue

        _summarizing_locks.add(path)
        try:
            messages = [
                {"role": "system", "content": _SUMMARIZE_SYSTEM_PROMPT},
                {"role": "user", "content": f"请总结以下对话：\n\n{raw[:4000]}"},
            ]
            summary_msg = await post_chat_completion(cfg, messages, tools=[])
            summary = (summary_msg.get("content") or "").strip()
            if summary and "无重要信息" not in summary:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M")
                _replace_raw_with_summary(path, ts, summary)
                results.append(f"【{_label}】✅ 已总结：\n{summary}")
                logger.info("[AIAgent] 会话记忆已总结并写入: %s", path)
            else:
                results.append(f"【{_label}】对话内容较日常，跳过总结")
        except Exception as e:
            results.append(f"【{_label}】❌ 总结失败：{e}")
            logger.warning("[AIAgent] 记忆总结异常: %s -> %s", path, e)
        finally:
            _summarizing_locks.discard(path)

    if not results:
        return "没有需要总结的记忆内容"
    return "\n\n".join(results)
