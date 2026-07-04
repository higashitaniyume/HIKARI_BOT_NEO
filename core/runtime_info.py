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
class VersionEntry:
    version: str
    git_hash: str
    title: str


@dataclass(frozen=True, slots=True)
class RuntimeInfo:
    current: VersionEntry
    versions: tuple[VersionEntry, ...]

    @property
    def version(self) -> str:
        return self.current.version

    @property
    def git_hash(self) -> str:
        return self.current.git_hash

    @property
    def title(self) -> str:
        return self.current.title


def get_runtime_info(*, refresh: bool = False) -> RuntimeInfo:
    """Return cached runtime version info, reading version.json first."""
    global _runtime_info_cache
    if refresh or _runtime_info_cache is None:
        _runtime_info_cache = read_runtime_info(PROJECT_ROOT)
    return _runtime_info_cache


def read_runtime_info(project_root: Path) -> RuntimeInfo:
    data = _read_version_json(project_root / VERSION_FILE_NAME)
    versions = _parse_version_entries(data.get("versions"))
    if not versions:
        versions = _legacy_entry(project_root, data) if _has_legacy_version_data(data) else _git_history_entries(project_root)
    if not versions:
        versions = _legacy_entry(project_root, data)
    current = versions[-1] if versions else VersionEntry("unknown", "unknown", "unknown")
    return RuntimeInfo(current=current, versions=tuple(versions))


def runtime_info_state(*, refresh: bool = False) -> dict[str, Any]:
    info = get_runtime_info(refresh=refresh)
    return {
        "current": _entry_state(info.current),
        "versions": [_entry_state(entry) for entry in info.versions],
    }


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
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as e:
        logger.warning("[RuntimeInfo] 读取版本文件失败: %s -> %s", path, e)
        return {}
    return data if isinstance(data, dict) else {}


def _parse_version_entries(value: Any) -> list[VersionEntry]:
    if not isinstance(value, list):
        return []

    entries: list[VersionEntry] = []
    seen_versions: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        version = _string_value(item.get("version"))
        git_hash = _string_value(item.get("git_hash"))
        title = _string_value(item.get("title"))
        if not version or not git_hash or not title or version in seen_versions:
            continue
        seen_versions.add(version)
        entries.append(VersionEntry(version=version, git_hash=git_hash, title=title))
    return entries


def _has_legacy_version_data(data: dict[str, Any]) -> bool:
    return any(key in data for key in ("version", "git_hash", "git_commit", "git_commit_short", "title"))


def _git_history_entries(project_root: Path) -> list[VersionEntry]:
    output = _run_git(project_root, "log", "--reverse", "--format=%h%x1f%s%x1e")
    entries: list[VersionEntry] = []
    for record in output.split("\x1e"):
        record = record.strip("\r\n")
        if not record or "\x1f" not in record:
            continue
        git_hash, title = record.split("\x1f", 1)
        entries.append(
            VersionEntry(
                version=f"0.0.{len(entries) + 1}",
                git_hash=git_hash.strip(),
                title=title.strip() or "unknown",
            )
        )
    return entries


def _legacy_entry(project_root: Path, data: dict[str, Any]) -> list[VersionEntry]:
    version = _string_value(data.get("version")) or _read_project_version(project_root) or "unknown"
    git_hash = (
        _string_value(data.get("git_hash"))
        or _string_value(data.get("git_commit_short"))
        or _short_commit(_string_value(data.get("git_commit")))
    )
    if not git_hash:
        git_hash = _run_git(project_root, "rev-parse", "--short=7", "HEAD") or "unknown"
    title = _string_value(data.get("title")) or _run_git(project_root, "log", "-1", "--format=%s") or "unknown"
    return [VersionEntry(version=version, git_hash=git_hash, title=title)]


def _entry_state(entry: VersionEntry) -> dict[str, str]:
    return {
        "version": entry.version,
        "git_hash": entry.git_hash,
        "title": entry.title,
    }


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
            timeout=2,
        )
    except Exception:
        return ""
    return result.stdout.decode("utf-8", errors="replace").strip()


def _short_commit(commit: str) -> str:
    commit = _string_value(commit)
    if not commit or commit == "unknown":
        return ""
    return commit[:7]


def _string_value(value: Any) -> str:
    return str(value or "").strip()
