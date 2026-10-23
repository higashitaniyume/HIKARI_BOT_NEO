from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..utils import safe_bool, safe_int

READ_PERSONA_RESOURCE = "read_persona_resource"

BOTDATA_PERSONA_ROOT = Path("BotData/agent_personas")
BOTDATA_PERSONA_EXTENSIONS = {".md", ".txt", ".json"}


def _tools_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    return cfg.get("tools") if isinstance(cfg.get("tools"), dict) else {}


def config(cfg: dict[str, Any]) -> dict[str, Any]:
    tools_cfg = _tools_cfg(cfg)
    return tools_cfg.get("files") if isinstance(tools_cfg.get("files"), dict) else {}


def enabled(cfg: dict[str, Any]) -> bool:
    return safe_bool(config(cfg).get("enabled"), True)


def definitions(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": READ_PERSONA_RESOURCE,
                "description": (
                    "Read a persona skill resource from BotData/agent_personas only. "
                    "Use this when the selected persona skill references another local markdown/text resource."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative path inside BotData/agent_personas, such as nuwa_hikari/tone.md.",
                        }
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        }
    ]


def can_handle(name: str) -> bool:
    return name == READ_PERSONA_RESOURCE


def _resolve_limited_path(root: Path, raw_path: Any) -> Path:
    value = str(raw_path or "").strip().replace("\\", "/")
    if not value or "\x00" in value:
        raise ValueError("path is required")
    candidate = Path(value)
    if candidate.is_absolute():
        raise ValueError("absolute paths are not allowed")
    root_path = (Path.cwd() / root).resolve()
    resolved = (root_path / candidate).resolve()
    if resolved != root_path and root_path not in resolved.parents:
        raise ValueError("path is outside allowed directory")
    return resolved


def _relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to((Path.cwd() / root).resolve()).as_posix()
    except ValueError:
        return path.name


def _is_allowed_persona_resource_path(path: Path) -> bool:
    root = (Path.cwd() / BOTDATA_PERSONA_ROOT).resolve()
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return False
    return path.suffix.lower() in BOTDATA_PERSONA_EXTENSIONS


def _read_text_file(root: Path, raw_path: Any, max_chars: int) -> str:
    path = _resolve_limited_path(root, raw_path)
    if not path.is_file():
        return json.dumps({"path": _relative_path(root, path), "error": "file not found"}, ensure_ascii=False)
    content = path.read_text(encoding="utf-8", errors="replace")
    return json.dumps(
        {
            "path": _relative_path(root, path),
            "content": content[:max_chars],
            "truncated": len(content) > max_chars,
        },
        ensure_ascii=False,
    )


def _read_persona_resource(cfg: dict[str, Any], arguments: dict[str, Any]) -> str:
    files_cfg = config(cfg)
    max_chars = safe_int(files_cfg.get("max_read_chars"), 20000, minimum=1000, maximum=200000)
    path = _resolve_limited_path(BOTDATA_PERSONA_ROOT, arguments.get("path"))
    if not _is_allowed_persona_resource_path(path):
        return json.dumps(
            {
                "path": _relative_path(BOTDATA_PERSONA_ROOT, path),
                "error": "only .md, .txt, and .json files under BotData/agent_personas are readable",
            },
            ensure_ascii=False,
        )
    return _read_text_file(BOTDATA_PERSONA_ROOT, arguments.get("path"), max_chars)


def execute(name: str, cfg: dict[str, Any], arguments: dict[str, Any]) -> str:
    if name == READ_PERSONA_RESOURCE:
        return _read_persona_resource(cfg, arguments)
    return json.dumps({"error": f"unknown file tool: {name}"}, ensure_ascii=False)
