from __future__ import annotations

import copy
import json
import logging
import re
from pathlib import Path
from typing import Any

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
        "fallback_prompt": "你是 HIKARI BOT 的聊天 AI Agent。请自然、简洁地回复用户。",
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
}


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


def load_persona_prompt(cfg: dict[str, Any]) -> str:
    persona_cfg = cfg.get("persona") if isinstance(cfg.get("persona"), dict) else {}
    fallback = str(persona_cfg.get("fallback_prompt") or DEFAULT_CONFIG["persona"]["fallback_prompt"]).strip()
    max_chars = safe_persona_max_chars(persona_cfg.get("max_chars"))
    try:
        path = resolve_persona_path(persona_cfg.get("skill_path"))
        skill_file = _candidate_skill_file(path)
        if skill_file is None:
            return fallback
        content = _read_text_file(skill_file, max_chars)
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
