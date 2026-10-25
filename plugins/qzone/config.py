"""QQ 空间插件配置。"""

from __future__ import annotations

import copy
from typing import Any

from core.access_control import DEFAULT_ACCESS_RULES
from core.config_loader import load_plugin_config

# 说说可见范围：public=公开，friends=好友可见，private=仅自己可见
VISIBLE_VALUES: dict[str, int] = {"public": 1, "friends": 3, "private": 4}
VISIBLE_LABELS: dict[int, str] = {1: "公开", 3: "好友可见", 4: "仅自己"}

DEFAULT_QZONE_CONFIG: dict[str, Any] = {
    "enabled": True,
    # 发说说 未显式指定可见范围时使用
    "default_visible": "public",
    # 单条说说最大字数（QQ 空间本身约 1000 字）
    "max_length": 1000,
    # 「空间动态」默认拉取条数
    "feed_count": 8,
    # 「好友说说」默认拉取条数
    "moods_count": 5,
    # 单次空间请求超时（秒）
    "timeout": 20,
    # 定时发说说（默认关闭，打开 enabled 后才会自动发）
    "schedule": {
        "enabled": False,
        "check_interval_seconds": 60,
        "grace_seconds": 1800,
        "state_path": "UserData/qzone_state.json",
        "ai": {
            "enabled": True,
            "temperature": 1.0,
            "max_tokens": 400,
            "timeout_seconds": 60,
            "max_chars": 120,
            # 人格提示词统一取 AI 配置里的 persona skill；这里只放额外的补充要求
            "extra_prompt": "",
        },
        "fallback_texts": [
            "哼，今天也在海里摸鱼喵～（摇尾巴）",
            "米饭煮好了哦，主人在忙什么呀？（晃尾）",
        ],
        "jobs": [
            {
                "id": "morning",
                "enabled": True,
                "time": "08:30",
                "days": [],
                "timezone": "Asia/Shanghai",
                "visible": "public",
                "prompt": "刚睡醒，准备开始新的一天，写一句早安或今天想做的小事",
            },
            {
                "id": "night",
                "enabled": True,
                "time": "22:30",
                "days": [],
                "timezone": "Asia/Shanghai",
                "visible": "public",
                "prompt": "准备睡觉了，写一句晚安或今天的小感想",
            },
        ],
    },
    "permissions": copy.deepcopy(DEFAULT_ACCESS_RULES),
}


def get_config() -> dict[str, Any]:
    cfg = load_plugin_config("qzone", DEFAULT_QZONE_CONFIG)
    default_visible = _normalize_visible(cfg.get("default_visible"))
    return {
        "enabled": _as_bool(cfg.get("enabled"), True),
        "default_visible": default_visible,
        "max_length": _clamp_int(cfg.get("max_length"), default=1000, minimum=1, maximum=5000),
        "feed_count": _clamp_int(cfg.get("feed_count"), default=8, minimum=1, maximum=20),
        "moods_count": _clamp_int(cfg.get("moods_count"), default=5, minimum=1, maximum=20),
        "timeout": _clamp_int(cfg.get("timeout"), default=20, minimum=5, maximum=120),
        "permissions": cfg.get("permissions") if isinstance(cfg.get("permissions"), dict) else {},
        "schedule": normalize_schedule(cfg.get("schedule")),
    }


_WEEKDAY_ALIASES: dict[str, int] = {
    "mon": 0,
    "monday": 0,
    "0": 0,
    "一": 0,
    "周一": 0,
    "星期一": 0,
    "tue": 1,
    "tuesday": 1,
    "1": 1,
    "二": 1,
    "周二": 1,
    "星期二": 1,
    "wed": 2,
    "wednesday": 2,
    "2": 2,
    "三": 2,
    "周三": 2,
    "星期三": 2,
    "thu": 3,
    "thursday": 3,
    "3": 3,
    "四": 3,
    "周四": 3,
    "星期四": 3,
    "fri": 4,
    "friday": 4,
    "4": 4,
    "五": 4,
    "周五": 4,
    "星期五": 4,
    "sat": 5,
    "saturday": 5,
    "5": 5,
    "六": 5,
    "周六": 5,
    "星期六": 5,
    "sun": 6,
    "sunday": 6,
    "6": 6,
    "日": 6,
    "天": 6,
    "周日": 6,
    "周天": 6,
    "星期日": 6,
    "星期天": 6,
}


def normalize_schedule(raw: Any) -> dict[str, Any]:
    """把 schedule 配置整理成可直接使用的结构（非法任务会被丢弃）。"""
    source = raw if isinstance(raw, dict) else {}
    ai_raw = source.get("ai") if isinstance(source.get("ai"), dict) else {}
    fallback = source.get("fallback_texts")
    fallback_texts = [
        str(item).strip()
        for item in (fallback if isinstance(fallback, list) else [])
        if str(item or "").strip()
    ]
    state_path = str(source.get("state_path") or "UserData/qzone_state.json").strip()
    return {
        "enabled": _as_bool(source.get("enabled"), False),
        "check_interval_seconds": _clamp_int(
            source.get("check_interval_seconds"), default=60, minimum=15, maximum=600
        ),
        "grace_seconds": _clamp_int(source.get("grace_seconds"), default=1800, minimum=60, maximum=21600),
        "state_path": state_path or "UserData/qzone_state.json",
        "ai": {
            "enabled": _as_bool(ai_raw.get("enabled"), True),
            "temperature": _clamp_float(ai_raw.get("temperature"), default=1.0, minimum=0.0, maximum=2.0),
            "max_tokens": _clamp_int(ai_raw.get("max_tokens"), default=400, minimum=64, maximum=4096),
            "timeout_seconds": _clamp_int(
                ai_raw.get("timeout_seconds"), default=60, minimum=5, maximum=600
            ),
            "max_chars": _clamp_int(ai_raw.get("max_chars"), default=120, minimum=10, maximum=1000),
            "extra_prompt": str(ai_raw.get("extra_prompt") or "").strip(),
        },
        "fallback_texts": fallback_texts,
        "jobs": parse_jobs(source.get("jobs")),
    }


def parse_jobs(raw: Any) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        job = parse_job(item)
        if job is None or job["id"] in seen:
            continue
        seen.add(job["id"])
        jobs.append(job)
    return jobs


def parse_job(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    job_id = str(raw.get("id") or "").strip()
    if not job_id:
        return None
    parsed_time = _parse_hhmm(raw.get("time"))
    if parsed_time is None:
        return None
    hour, minute = parsed_time
    visible = _normalize_visible(raw.get("visible"))
    return {
        "id": job_id,
        "enabled": _as_bool(raw.get("enabled"), True),
        "time": f"{hour:02d}:{minute:02d}",
        "hour": hour,
        "minute": minute,
        "days": _parse_days(raw.get("days")),
        "timezone": str(raw.get("timezone") or "Asia/Shanghai").strip() or "Asia/Shanghai",
        "visible": visible,
        "visible_label": VISIBLE_LABELS[VISIBLE_VALUES[visible]],
        "prompt": str(raw.get("prompt") or "").strip(),
    }


def _parse_hhmm(value: Any) -> tuple[int, int] | None:
    text = str(value or "").strip()
    if ":" not in text:
        return None
    hour_text, _, minute_text = text.partition(":")
    try:
        hour, minute = int(hour_text), int(minute_text)
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def _parse_days(value: Any) -> list[int]:
    items = value if isinstance(value, list) else []
    days: list[int] = []
    for item in items:
        token = str(item).strip().casefold()
        day = _WEEKDAY_ALIASES.get(token)
        if day is not None and day not in days:
            days.append(day)
    return sorted(days)


def _normalize_visible(value: Any) -> str:
    text = str(value or "").strip().casefold()
    return text if text in VISIBLE_VALUES else "public"


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _clamp_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _clamp_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)
