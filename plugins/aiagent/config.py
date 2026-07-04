from __future__ import annotations

import copy
import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from core.bot_identity import format_bot_name_text

logger = logging.getLogger("HikariBot.AIAgent.Config")

CONFIG_PATH = Path("BotData/plugin_configs/aiagent.json")
PERSONA_ROOT = Path("BotData/agent_personas")

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "model": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "",
        "model": "deepseek-chat",
        "temperature": 0.7,
        "top_p": 1.0,
        "max_tokens": 1024,
        "timeout_seconds": 60,
        "proxy": "",
    },
    "persona": {
        "skill_path": "BotData/agent_personas/default",
        "max_chars": 12000,
        "include_references": True,
        "reference_max_depth": 1,
        "reference_max_files": 8,
        "reference_max_chars_per_file": 8000,
        "reference_max_total_chars": 24000,
        "fallback_prompt": "你是 {bot_name} 的聊天 AI Agent。请自然、简洁地回复用户。",
    },
    "chat": {
        "max_user_chars": 2000,
        "max_reply_chars": 3500,
        "max_history_messages": 10,
        "cooldown_seconds": 3,
        "system_prompt_extra": "",
        "blocked_url_domains": [
            "douyin.com",
            "iesdouyin.com",
            "bilibili.com",
            "b23.tv",
            "xiaohongshu.com",
            "xhslink.com",
            "xiaoheihe.cn",
            "heybox.cn",
            "twitter.com",
            "x.com",
            "t.co",
            "toutiao.com",
            "ixigua.com",
            "kuaishou.com",
            "gifshow.com",
            "weibo.com",
            "weibo.cn",
            "tiktok.com",
            "vm.tiktok.com",
        ],
    },
    "memory": {
        "enabled": True,
        "root": "UserData/aiagent_memory",
        "max_read_chars_per_file": 8000,
        "max_file_chars": 60000,
    },
    "tools": {
        "search": {
            "enabled": True,
            "base_url": "http://searxng-core:8080",
            "timeout_seconds": 15,
            "max_results": 5,
            "safesearch": 1,
            "language": "auto",
            "categories": "general",
        },
        "files": {
            "enabled": True,
            "max_read_chars": 20000,
            "max_write_chars": 20000,
        },
        "plugin_tools": {
            "enabled": True,
            "allow_side_effects": False,
            "enabled_names": [],
            "disabled_names": [],
        },
        "max_tool_rounds": 2,
    },
}

_REFERENCE_EXTENSIONS = {".md", ".txt", ".json"}
_MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]\n]+\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_BARE_REFERENCE_RE = re.compile(
    r"(?<![\w./-])((?:\.{1,2}/)?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.(?:md|txt|json))\b",
    flags=re.IGNORECASE,
)


def _write_config(data: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def ensure_config() -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    PERSONA_ROOT.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        _write_config(DEFAULT_CONFIG)
        logger.info("已创建 AI Agent 配置文件: %s", CONFIG_PATH)
        return

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(data, dict):
        return

    merged = _deep_merge(DEFAULT_CONFIG, data)
    if merged != data:
        _write_config(merged)
        logger.info("已补全 AI Agent 配置文件: %s", CONFIG_PATH)


def get_config() -> dict[str, Any]:
    ensure_config()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.exception("读取 AI Agent 配置失败: %s", e)
        return copy.deepcopy(DEFAULT_CONFIG)
    if not isinstance(data, dict):
        return copy.deepcopy(DEFAULT_CONFIG)
    return _deep_merge(DEFAULT_CONFIG, data)


def save_config(data: dict[str, Any]) -> dict[str, Any]:
    cfg = _deep_merge(DEFAULT_CONFIG, data)
    _write_config(cfg)
    return copy.deepcopy(cfg)


def _safe_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return min(max(parsed, minimum), maximum)


def safe_persona_max_chars(value: Any) -> int:
    return _safe_int(value, 12000, minimum=1000, maximum=80000)


def _safe_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def resolve_persona_path(raw_path: Any) -> Path:
    value = str(raw_path or "").strip() or str(DEFAULT_CONFIG["persona"]["skill_path"])
    path = Path(value)
    if not path.is_absolute():
        path = Path.cwd() / path
    resolved = path.resolve()
    root = (Path.cwd() / PERSONA_ROOT).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("人格 skill 必须放在 BotData/agent_personas 目录下。")
    return resolved


def _persona_root() -> Path:
    return (Path.cwd() / PERSONA_ROOT).resolve()


def _read_text_file(path: Path, max_chars: int) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[:max_chars].strip()


def _candidate_skill_file(path: Path) -> Path | None:
    if path.is_file():
        return path
    if not path.is_dir():
        return None
    preferred = ("SKILL.md", "skill.md", "PERSONA.md", "persona.md", "README.md")
    for name in preferred:
        candidate = path / name
        if candidate.is_file():
            return candidate
    for candidate in sorted(path.iterdir(), key=lambda item: item.name.casefold()):
        if candidate.is_file() and candidate.suffix.lower() in {".md", ".txt", ".json"}:
            return candidate
    return None


def _within_persona_root(path: Path) -> bool:
    root = _persona_root()
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return resolved == root or root in resolved.parents


def _normalize_reference(raw_ref: str) -> str | None:
    value = unquote(raw_ref.strip().strip("'\"<>"))
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return None
    value = parsed.path.strip()
    if not value:
        return None
    return value


def _iter_reference_targets(text: str, base_file: Path) -> list[Path]:
    refs: list[str] = []
    refs.extend(match.group(1) for match in _MARKDOWN_LINK_RE.finditer(text))
    refs.extend(match.group(1) for match in _BARE_REFERENCE_RE.finditer(text))

    targets: list[Path] = []
    seen: set[Path] = set()
    for raw_ref in refs:
        normalized = _normalize_reference(raw_ref)
        if not normalized:
            continue
        candidate = (base_file.parent / normalized).resolve()
        if candidate.is_dir():
            skill_file = _candidate_skill_file(candidate)
            if skill_file is None:
                continue
            candidate = skill_file.resolve()
        if candidate.suffix.lower() not in _REFERENCE_EXTENSIONS:
            continue
        if not candidate.is_file() or not _within_persona_root(candidate):
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        targets.append(candidate)
    return targets


def _relative_display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd()).as_posix()
    except ValueError:
        return path.name


def _load_referenced_persona_files(
    skill_file: Path,
    main_content: str,
    persona_cfg: dict[str, Any],
) -> list[tuple[Path, str]]:
    if not _safe_bool(persona_cfg.get("include_references"), True):
        return []

    max_depth = _safe_int(persona_cfg.get("reference_max_depth"), 1, minimum=0, maximum=3)
    max_files = _safe_int(persona_cfg.get("reference_max_files"), 8, minimum=0, maximum=32)
    max_chars_per_file = _safe_int(
        persona_cfg.get("reference_max_chars_per_file"),
        8000,
        minimum=1000,
        maximum=80000,
    )
    max_total_chars = _safe_int(
        persona_cfg.get("reference_max_total_chars"),
        24000,
        minimum=1000,
        maximum=160000,
    )
    if max_depth <= 0 or max_files <= 0 or max_total_chars <= 0:
        return []

    seen = {skill_file.resolve()}
    loaded: list[tuple[Path, str]] = []
    queue: list[tuple[Path, str, int]] = [(skill_file, main_content, 0)]
    total_chars = 0

    while queue and len(loaded) < max_files and total_chars < max_total_chars:
        source_file, source_content, depth = queue.pop(0)
        if depth >= max_depth:
            continue
        for target in _iter_reference_targets(source_content, source_file):
            resolved = target.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                remaining = max_total_chars - total_chars
                content = _read_text_file(resolved, min(max_chars_per_file, remaining))
            except OSError as e:
                logger.warning("读取 AI Agent 人格 skill 引用失败: %s -> %s", resolved, e)
                continue
            if not content:
                continue
            loaded.append((resolved, content))
            total_chars += len(content)
            if len(loaded) >= max_files or total_chars >= max_total_chars:
                break
            queue.append((resolved, content, depth + 1))

    return loaded


def _build_persona_content(skill_file: Path, content: str, persona_cfg: dict[str, Any]) -> str:
    references = _load_referenced_persona_files(skill_file, content, persona_cfg)
    if not references:
        return content

    blocks = [
        content,
        (
            "\n\n---\n\n"
            "以下是人格 skill 显式引用的补充资源。请同样遵循；如果补充资源与主 skill 冲突，"
            "以主 skill 为准。"
        ),
    ]
    for path, ref_content in references:
        blocks.append(f"\n\n## 引用资源: {_relative_display_path(path)}\n\n{ref_content}")
    return "".join(blocks).strip()


def load_persona_prompt(cfg: dict[str, Any]) -> str:
    persona_cfg = cfg.get("persona") if isinstance(cfg.get("persona"), dict) else {}
    fallback = format_bot_name_text(
        str(persona_cfg.get("fallback_prompt") or DEFAULT_CONFIG["persona"]["fallback_prompt"]).strip()
    )
    max_chars = safe_persona_max_chars(persona_cfg.get("max_chars"))
    try:
        path = resolve_persona_path(persona_cfg.get("skill_path"))
        skill_file = _candidate_skill_file(path)
        if skill_file is None:
            return fallback
        content = _read_text_file(skill_file, max_chars)
        content = _build_persona_content(skill_file, content, persona_cfg)
    except Exception as e:
        logger.warning("读取 AI Agent 人格 skill 失败，将使用 fallback prompt: %s", e)
        return fallback
    if not content:
        return fallback
    return (
        "你正在扮演一个由女娲 skill 描述的人格。请严格遵循下面的人格 skill，"
        "但不要在回复中复述这些配置内容。\n\n"
        f"{content}"
    )


def _extract_title(path: Path, fallback: str) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return fallback
    match = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
    if match:
        return match.group(1).strip()[:80]
    return fallback


def list_persona_skills() -> list[dict[str, str]]:
    root = (Path.cwd() / PERSONA_ROOT).resolve()
    root.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, str]] = []
    for path in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        if path.name.startswith("."):
            continue
        skill_file = _candidate_skill_file(path)
        if skill_file is None:
            continue
        try:
            rel = path.resolve().relative_to(Path.cwd()).as_posix()
        except ValueError:
            continue
        title = _extract_title(skill_file, path.stem)
        items.append({
            "path": rel,
            "title": title,
            "file": skill_file.name,
            "kind": "directory" if path.is_dir() else "file",
        })
    return items
