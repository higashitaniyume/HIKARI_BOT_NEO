"""Runtime version and uptime helpers for HIKARI BOT NEO."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("HikariBot.RuntimeInfo")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE_NAME = "version.json"
PROCESS_STARTED_AT_MONOTONIC = time.monotonic()

_runtime_info_cache: "RuntimeInfo | None" = None


@dataclass(frozen=True, slots=True)
class RuntimeInfo:
    version: str
    git_commit: str
    git_commit_short: str
    git_dirty: bool
    generated_at: str


def get_runtime_info(*, refresh: bool = False) -> RuntimeInfo:
    """Return cached runtime version info, reading version.json first."""
    global _runtime_info_cache
    if refresh or _runtime_info_cache is None:
        _runtime_info_cache = read_runtime_info(PROJECT_ROOT)
    return _runtime_info_cache


def read_runtime_info(project_root: Path) -> RuntimeInfo:
    data = _read_version_json(project_root / VERSION_FILE_NAME)
    version = _string_value(data.get("version")) or _read_project_version(project_root) or "unknown"
    git_commit = _string_value(data.get("git_commit"))
    git_commit_short = _string_value(data.get("git_commit_short"))
    git_dirty = bool(data.get("git_dirty", False))
    generated_at = _string_value(data.get("generated_at"))

    if not git_commit:
        git_commit = _run_git(project_root, "rev-parse", "HEAD") or "unknown"
    if not git_commit_short:
        git_commit_short = _short_commit(git_commit)
    if "git_dirty" not in data:
        git_dirty = bool(_run_git(project_root, "status", "--porcelain"))

    return RuntimeInfo(
        version=version,
        git_commit=git_commit,
        git_commit_short=git_commit_short,
        git_dirty=git_dirty,
        generated_at=generated_at or "unknown",
    )


def get_uptime_seconds() -> int:
    return max(0, int(time.monotonic() - PROCESS_STARTED_AT_MONOTONIC))


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小时")
    if minutes:
        parts.append(f"{minutes}分钟")
    if not parts:
        parts.append(f"{secs}秒")
    return "".join(parts)


def _read_version_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("[RuntimeInfo] 读取版本文件失败: %s -> %s", path, e)
        return {}
    return data if isinstance(data, dict) else {}


def _read_project_version(project_root: Path) -> str:
    pyproject = project_root / "pyproject.toml"
    if not pyproject.is_file():
        return ""

    try:
        import tomllib  # type: ignore[import-not-found]

        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        project = data.get("project") if isinstance(data, dict) else None
        if isinstance(project, dict):
            return _string_value(project.get("version"))
    except ModuleNotFoundError:
        pass
    except Exception as e:
        logger.debug("[RuntimeInfo] tomllib 读取 pyproject 失败: %s", e)

    in_project = False
    try:
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped == "[project]":
                in_project = True
                continue
            if in_project and stripped.startswith("["):
                break
            if in_project:
                match = re.match(r'version\s*=\s*"([^"]+)"', stripped)
                if match:
                    return match.group(1).strip()
    except Exception as e:
        logger.debug("[RuntimeInfo] 文本读取 pyproject 失败: %s", e)
    return ""


def _run_git(project_root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return ""
    return result.stdout.strip()


def _short_commit(commit: str) -> str:
    commit = _string_value(commit)
    if not commit or commit == "unknown":
        return "unknown"
    return commit[:7]


def _string_value(value: Any) -> str:
    return str(value or "").strip()
