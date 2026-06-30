from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from core.access_control import normalize_access_rules
from plugins.push_framework.config import get_config as get_push_config
from plugins.push_framework.registry import iter_push_sources
from plugins.rss_subscriber.config import get_config as get_rss_config
from plugins.rss_subscriber.config import save_config as save_rss_config

from .constants import (
    _ACCESS_RULE_PLUGINS,
    _LOG_DIR,
    _MAX_CONFIG_EDIT_BYTES,
    _MAX_LOG_TAIL_BYTES,
    _PLUGIN_CONFIG_DIR,
)
from .parsing import _parse_bool, _parse_float, _parse_int, _parse_str

def _safe_config_file(name: str) -> Path:
    raw_name = unquote(str(name or "")).strip()
    safe_name = Path(raw_name).name
    if raw_name != safe_name or not safe_name.endswith(".json") or safe_name.startswith("."):
        raise ValueError("配置文件名无效。")
    path = (_PLUGIN_CONFIG_DIR / safe_name).resolve()
    root = _PLUGIN_CONFIG_DIR.resolve()
    if root not in path.parents or not path.is_file():
        raise ValueError("配置文件不存在。")
    return path


def _safe_log_file(name: str) -> Path:
    raw_name = unquote(str(name or "")).strip()
    safe_name = Path(raw_name).name
    if raw_name != safe_name or not safe_name.endswith(".log") or safe_name.startswith("."):
        raise ValueError("日志文件名无效。")
    path = (_LOG_DIR / safe_name).resolve()
    root = _LOG_DIR.resolve()
    if root not in path.parents or not path.is_file():
        raise ValueError("日志文件不存在。")
    return path


def _file_meta(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
    }


def _list_plugin_configs() -> dict[str, Any]:
    _PLUGIN_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    files = [
        _file_meta(path)
        for path in sorted(_PLUGIN_CONFIG_DIR.glob("*.json"), key=lambda item: item.name.casefold())
        if path.is_file() and not path.name.endswith(".example.json")
    ]
    return {
        "files": files,
        "max_edit_bytes": _MAX_CONFIG_EDIT_BYTES,
    }


def _read_plugin_config(name: str) -> dict[str, Any]:
    path = _safe_config_file(name)
    size = path.stat().st_size
    if size > _MAX_CONFIG_EDIT_BYTES:
        raise ValueError(f"配置文件太大，暂不支持在线编辑：{path.name}")
    text = path.read_text(encoding="utf-8")
    try:
        json.loads(text or "{}")
    except json.JSONDecodeError as e:
        raise ValueError(f"配置文件 JSON 格式错误：{e}") from e
    return {
        "file": _file_meta(path),
        "content": text,
    }


def _write_plugin_config(name: str, content: str) -> dict[str, Any]:
    path = _safe_config_file(name)
    if len(content.encode("utf-8")) > _MAX_CONFIG_EDIT_BYTES:
        raise ValueError("配置内容太大，拒绝保存。")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 格式错误：{e}") from e
    if not isinstance(parsed, dict):
        raise ValueError("配置文件顶层必须是 JSON 对象。")

    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)
    return _read_plugin_config(path.name)


def _push_sources_state() -> list[dict[str, Any]]:
    return [
        {
            "name": source.name,
            "description": source.description,
            "default_options": source.default_options or {},
        }
        for source in iter_push_sources()
    ]


def _push_config_state() -> dict[str, Any]:
    cfg = get_push_config()
    path = _PLUGIN_CONFIG_DIR / "push_framework.json"
    return {
        "config": cfg,
        "sources": _push_sources_state(),
        "file": _file_meta(path) if path.is_file() else None,
    }


def _rss_config_state() -> dict[str, Any]:
    cfg = get_rss_config()
    path = _PLUGIN_CONFIG_DIR / "rss_subscriber.json"
    return {
        "config": cfg,
        "file": _file_meta(path) if path.is_file() else None,
    }


def _write_rss_config(data: dict[str, Any]) -> dict[str, Any]:
    saved = save_rss_config(data)
    payload = _rss_config_state()
    payload["config"] = saved
    payload["message"] = "RSS 订阅设置已保存。"
    return payload


def _parse_push_time(value: Any, *, default: str = "09:00") -> str:
    text = str(value or default).strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{1,2})", text)
    if not match:
        raise ValueError(f"推送时间格式无效：{text}")
    hour = int(match.group(1))
    minute = int(match.group(2))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"推送时间超出范围：{text}")
    return f"{hour:02d}:{minute:02d}"


def _parse_push_string_list(value: Any, *, max_items: int = 31, max_length: int = 24) -> list[str]:
    if isinstance(value, str):
        raw_items = re.split(r"[\s,，;；]+", value)
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    result: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        if len(text) > max_length:
            raise ValueError(f"列表项过长：{text[:20]}...")
        seen.add(text)
        result.append(text)
        if len(result) > max_items:
            raise ValueError("列表项过多。")
    return result


def _parse_push_id_list(value: Any) -> list[int]:
    if isinstance(value, str):
        raw_items = re.split(r"[\s,，;；]+", value)
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    result: list[int] = []
    seen: set[int] = set()
    for item in raw_items:
        text = str(item or "").strip()
        if not text:
            continue
        try:
            target_id = int(text)
        except ValueError as e:
            raise ValueError(f"推送目标 ID 无效：{text}") from e
        if target_id <= 0:
            raise ValueError(f"推送目标 ID 必须大于 0：{text}")
        if target_id in seen:
            continue
        seen.add(target_id)
        result.append(target_id)
        if len(result) > 200:
            raise ValueError("单个任务最多配置 200 个推送目标。")
    return result


def _normalize_push_job(raw_job: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw_job, dict):
        raise ValueError(f"第 {index + 1} 个推送任务必须是 JSON 对象。")

    job_id = _parse_str(raw_job.get("id"), max_length=80)
    if not job_id:
        raise ValueError(f"第 {index + 1} 个推送任务缺少 ID。")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", job_id):
        raise ValueError(f"推送任务 ID 只能包含字母、数字、下划线、短横线和点：{job_id}")

    source = _parse_str(raw_job.get("source"), max_length=80)
    if not source:
        raise ValueError(f"推送任务 {job_id} 缺少消息源。")

    trigger = str(raw_job.get("trigger") or "schedule").strip().casefold()
    if trigger not in {"schedule", "startup", "shutdown", "manual"}:
        raise ValueError(f"推送任务 {job_id} 的 trigger 只能是 schedule、startup、shutdown 或 manual。")

    times = [_parse_push_time(item) for item in _parse_push_string_list(raw_job.get("times"), max_items=24)]
    time_value = _parse_push_time(raw_job.get("time") or (times[0] if times else "09:00"))
    days = _parse_push_string_list(raw_job.get("days"), max_items=7, max_length=16)
    targets = raw_job.get("targets") if isinstance(raw_job.get("targets"), dict) else {}
    source_options = raw_job.get("source_options")
    if source_options is None:
        source_options = {}
    if not isinstance(source_options, dict):
        raise ValueError(f"推送任务 {job_id} 的 source_options 必须是 JSON 对象。")

    dedupe = str(raw_job.get("dedupe") or "daily").strip().casefold()
    if dedupe not in {"daily", "none"}:
        raise ValueError(f"推送任务 {job_id} 的 dedupe 只能是 daily 或 none。")

    return {
        "id": job_id,
        "enabled": _parse_bool(raw_job.get("enabled", True)),
        "trigger": trigger,
        "source": source,
        "time": time_value,
        "times": times,
        "timezone": _parse_str(raw_job.get("timezone", "Asia/Shanghai"), "Asia/Shanghai", max_length=80)
        or "Asia/Shanghai",
        "days": days,
        "late_grace_seconds": _parse_int(
            raw_job.get("late_grace_seconds", 7200),
            7200,
            minimum=0,
            maximum=86400,
        ),
        "dedupe": dedupe,
        "targets": {
            "group_ids": _parse_push_id_list(targets.get("group_ids")),
            "private_user_ids": _parse_push_id_list(targets.get("private_user_ids")),
        },
        "source_options": source_options,
    }


def _normalize_push_config(data: dict[str, Any]) -> dict[str, Any]:
    current = get_push_config()
    raw_jobs = data.get("jobs", current.get("jobs", []))
    if not isinstance(raw_jobs, list):
        raise ValueError("jobs 必须是数组。")
    jobs = [_normalize_push_job(job, index) for index, job in enumerate(raw_jobs)]
    seen_ids: set[str] = set()
    for job in jobs:
        if job["id"] in seen_ids:
            raise ValueError(f"推送任务 ID 重复：{job['id']}")
        seen_ids.add(job["id"])

    return {
        "enabled": _parse_bool(data.get("enabled", current.get("enabled", True))),
        "startup_delay_seconds": _parse_int(
            data.get("startup_delay_seconds", current.get("startup_delay_seconds", 15)),
            15,
            minimum=0,
            maximum=3600,
        ),
        "check_interval_seconds": _parse_int(
            data.get("check_interval_seconds", current.get("check_interval_seconds", 60)),
            60,
            minimum=10,
            maximum=86400,
        ),
        "send_retry_attempts": _parse_int(
            data.get("send_retry_attempts", current.get("send_retry_attempts", 2)),
            2,
            minimum=1,
            maximum=10,
        ),
        "send_retry_delay_seconds": _parse_float(
            data.get("send_retry_delay_seconds", current.get("send_retry_delay_seconds", 2.0)),
            2.0,
            minimum=0,
            maximum=120,
        ),
        "jobs": jobs,
    }


def _write_push_config(data: dict[str, Any]) -> dict[str, Any]:
    _PLUGIN_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    get_push_config()
    normalized = _normalize_push_config(data)
    _write_plugin_config("push_framework.json", json.dumps(normalized, ensure_ascii=False, indent=2))
    payload = _push_config_state()
    payload["message"] = "推送配置已保存。"
    return payload


def _push_run_payload(result: Any) -> dict[str, Any]:
    return {
        "job_id": result.job_id,
        "source": result.source,
        "attempted": result.attempted,
        "sent": result.sent,
        "skipped": result.skipped,
        "empty": result.empty,
        "failed": result.failed,
        "errors": result.errors,
    }


def _access_rule_item(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError as e:
        raise ValueError(f"{path.name} JSON 格式错误：{e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} 顶层必须是 JSON 对象。")
    return {
        "name": path.name,
        "label": _ACCESS_RULE_PLUGINS.get(path.name, path.stem),
        "permissions": normalize_access_rules(data.get("permissions", {})),
        "mtime": path.stat().st_mtime,
    }


def _access_rules_state() -> dict[str, Any]:
    _PLUGIN_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    plugins: list[dict[str, Any]] = []
    for file_name in _ACCESS_RULE_PLUGINS:
        path = _PLUGIN_CONFIG_DIR / file_name
        if path.is_file():
            plugins.append(_access_rule_item(path))
    return {"plugins": plugins}


def _write_access_rules(data: dict[str, Any]) -> dict[str, Any]:
    name = str(data.get("plugin") or "").strip()
    if name not in _ACCESS_RULE_PLUGINS:
        raise ValueError("不支持管理这个插件的权限。")
    path = _safe_config_file(name)
    try:
        current = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError as e:
        raise ValueError(f"配置文件 JSON 格式错误：{e}") from e
    if not isinstance(current, dict):
        raise ValueError("配置文件顶层必须是 JSON 对象。")

    current["permissions"] = normalize_access_rules(data.get("permissions", {}))
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)
    payload = _access_rules_state()
    payload["message"] = "权限规则已保存。"
    return payload


def _list_logs() -> dict[str, Any]:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    files = [
        _file_meta(path)
        for path in sorted(_LOG_DIR.glob("*.log"), key=lambda item: item.stat().st_mtime, reverse=True)
        if path.is_file()
    ]
    return {
        "files": files,
        "max_tail_bytes": _MAX_LOG_TAIL_BYTES,
    }


def _read_log_tail(name: str, max_bytes: Any = _MAX_LOG_TAIL_BYTES) -> dict[str, Any]:
    path = _safe_log_file(name)
    try:
        requested = int(max_bytes)
    except Exception:
        requested = _MAX_LOG_TAIL_BYTES
    limit = min(max(requested, 1024), _MAX_LOG_TAIL_BYTES)
    size = path.stat().st_size
    with path.open("rb") as f:
        if size > limit:
            f.seek(size - limit)
        content = f.read(limit)
    return {
        "file": _file_meta(path),
        "truncated": size > limit,
        "content": content.decode("utf-8", errors="replace"),
    }

