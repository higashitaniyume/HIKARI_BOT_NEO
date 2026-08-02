from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from ..utils import safe_bool

logger = logging.getLogger("HikariBot.AIAgent.Tools.Help")

TOOL_NAME = "bot_help"

# 仓库 docs/ 目录：机器人功能文档，随源码部署
_DOCS_ROOT = Path("docs")

# 索引条目：文件名 → (一句话描述, 检索权重)。
# API.md 是面向开发者的 HTTP 接口文档，回答"功能怎么用"类问题时降权，
# 避免其大量 API 章节标题压过用户功能文档。
_DOC_INDEX: list[tuple[str, tuple[str, float]]] = [
    ("overview.md", ("项目简介、架构、数据边界", 1.0)),
    ("deployment.md", ("部署与本地开发", 1.0)),
    ("plugins.md", ("全部插件功能与命令详解", 1.0)),
    ("astrbot.md", ("AstrBot 插件兼容层", 1.0)),
    ("core.md", ("核心模块与开发模式", 1.0)),
    ("resources.md", ("可热改资源（字体、固定回复）", 1.0)),
    ("faq.md", ("常见问题与验证命令", 1.0)),
    ("API.md", ("后台 HTTP API 文档", 0.4)),
]

_MAX_RETURN_CHARS = 12000
_TITLE_RE = re.compile(r"^#\s+(.+)$", flags=re.MULTILINE)


def _tools_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    return cfg.get("tools") if isinstance(cfg.get("tools"), dict) else {}


def config(cfg: dict[str, Any]) -> dict[str, Any]:
    tools_cfg = _tools_cfg(cfg)
    return tools_cfg.get("help") if isinstance(tools_cfg.get("help"), dict) else {}


def enabled(cfg: dict[str, Any]) -> bool:
    return safe_bool(config(cfg).get("enabled"), True)


def definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": (
                "查询本机器人的功能帮助文档（它能干什么、有哪些功能、某个功能怎么使用、"
                "有哪些命令）。当用户询问机器人的能力、功能列表或某个具体功能的用法时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": (
                            "想了解的功能关键词或文档名（如 贴纸、AI聊天、部署）。"
                            "留空时返回全部文档索引。"
                        ),
                    },
                },
                "required": [],
            },
        },
    }


def _read_doc(path: Path, max_chars: int = _MAX_RETURN_CHARS) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[:max_chars].strip()


def _doc_title(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return path.stem
    match = _TITLE_RE.search(text)
    return match.group(1).strip()[:60] if match else path.stem


def _index_payload() -> str:
    lines = ["以下是本机器人的功能文档列表，可用 bot_help 按文档名或关键词继续查询：", ""]
    for name, (desc, _weight) in _DOC_INDEX:
        lines.append(f"- {name}：{desc}")
    return "\n".join(lines)


_TOKEN_SPLIT_RE = re.compile(r"[\s,，。；;、/\\|·]+")
_SUB_TOKEN_RE = re.compile(r"[a-z0-9]+|[^\x00-\x7f]+")


def _topic_tokens(topic_lower: str) -> list[str]:
    """把查询词拆成检索 token（按标点切分后，再把 ASCII 与中文段拆开，
    例如「ai聊天」→ [\"ai\", \"聊天\"]）。"""
    tokens: list[str] = []
    for chunk in _TOKEN_SPLIT_RE.split(topic_lower):
        for sub in _SUB_TOKEN_RE.findall(chunk.strip()):
            if sub:
                tokens.append(sub)
    return tokens


def _match_score(topic_lower: str, tokens: list[str], name: str, desc: str, title: str, content_lower: str) -> int:
    """文档相关性打分：文件名 > 描述 > 标题 > 章节标题行 > 正文出现次数。"""
    score = 0
    if topic_lower == name.casefold():
        score += 500
    elif topic_lower in name.casefold():
        score += 200
    if topic_lower in desc.casefold():
        score += 100
    if topic_lower in title.casefold():
        score += 80
    # 章节标题行（## 本地贴纸包 等）是强信号，比正文计数权重高
    heading_hits = len(re.findall(rf"^#{{1,3}}[^\n]*{re.escape(topic_lower)}", content_lower, re.MULTILINE))
    score += heading_hits * 25
    if topic_lower in content_lower:
        score += content_lower.count(topic_lower) * 4
    for token in tokens:
        if token in name.casefold():
            score += 40
        if token in desc.casefold():
            score += 20
        if token in title.casefold():
            score += 15
        if token in content_lower:
            score += content_lower.count(token)
    return score


def execute(cfg: dict[str, Any], arguments: dict[str, Any]) -> str:
    """按 topic 返回 docs/ 下最相关的文档；无 topic 返回文档索引。"""
    topic = str(arguments.get("topic") or "").strip()
    if not topic:
        return _index_payload()

    root = _DOCS_ROOT.resolve()
    topic_lower = topic.casefold()
    tokens = _topic_tokens(topic_lower)

    best: tuple[str, str] | None = None
    best_score = 0
    for name, (desc, weight) in _DOC_INDEX:
        path = root / name
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")[:_MAX_RETURN_CHARS]
        except OSError:
            continue
        title = _doc_title(path).casefold()
        score = _match_score(topic_lower, tokens, name, desc, title, content.casefold())
        score = int(score * weight)
        if score > best_score:
            best_score = score
            best = (name, content)

    if best is not None and best_score > 0:
        name, content = best
        return f"# 文档: {name}\n\n{content}"

    # 没有匹配 → 返回索引，让模型继续引导
    return f"没有找到与「{topic}」相关的文档。\n\n{_index_payload()}"
