from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nonebot.adapters.onebot.v11.exception import ActionFailed

from .config import get_config
from .registry import PushContext, PushTarget, build_push_messages, get_push_source
from .storage import mark_sent, was_sent

logger = logging.getLogger("HikariBot.PushFramework.Scheduler")

_WEEKDAY_ALIASES = {
    "mon": 0,
    "monday": 0,
    "1": 0,
    "一": 0,
    "周一": 0,
    "星期一": 0,
    "tue": 1,
    "tuesday": 1,
    "2": 1,
    "二": 1,
    "周二": 1,
    "星期二": 1,
    "wed": 2,
    "wednesday": 2,
    "3": 2,
    "三": 2,
    "周三": 2,
    "星期三": 2,
    "thu": 3,
    "thursday": 3,
    "4": 3,
    "四": 3,
    "周四": 3,
    "星期四": 3,
    "fri": 4,
    "friday": 4,
    "5": 4,
    "五": 4,
    "周五": 4,
    "星期五": 4,
    "sat": 5,
    "saturday": 5,
    "6": 5,
    "六": 5,
    "周六": 5,
    "星期六": 5,
    "sun": 6,
    "sunday": 6,
    "7": 6,
    "日": 6,
    "天": 6,
    "周日": 6,
    "周天": 6,
    "星期日": 6,
    "星期天": 6,
}

_FIXED_TIMEZONES: dict[str, tzinfo] = {
    "Asia/Shanghai": timezone(timedelta(hours=8), "Asia/Shanghai"),
    "PRC": timezone(timedelta(hours=8), "Asia/Shanghai"),
}

_TRIGGERS = {"schedule", "startup", "shutdown", "manual"}
_MEDIA_SEGMENT_TYPES = {"image", "record", "video", "file"}


@dataclass(slots=True)
class PushRunResult:
    job_id: str
    source: str
    attempted: int = 0
    sent: int = 0
    skipped: int = 0
    empty: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def find_job_by_id(job_id: str, config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    target_id = str(job_id or "").strip()
    if not target_id:
        return None
    cfg = config or get_config()
    jobs = cfg.get("jobs") if isinstance(cfg.get("jobs"), list) else []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        if str(job.get("id") or "").strip() == target_id:
            return job
    return None


def normalize_targets(job: dict[str, Any]) -> list[PushTarget]:
    targets = job.get("targets") if isinstance(job.get("targets"), dict) else {}
    result: list[PushTarget] = []
    result.extend(PushTarget("group", target_id) for target_id in _normalize_ids(targets.get("group_ids")))
    result.extend(PushTarget("private", target_id) for target_id in _normalize_ids(targets.get("private_user_ids")))
    return result


def is_job_due(job: dict[str, Any], now: datetime | None = None) -> tuple[bool, str]:
    tokens = due_job_tokens(job, now=now)
    return (bool(tokens), tokens[0] if tokens else "")


def due_job_tokens(job: dict[str, Any], now: datetime | None = None) -> list[str]:
    if not bool(job.get("enabled", True)):
        return []
    if job_trigger(job) != "schedule":
        return []

    tz = _timezone(str(job.get("timezone") or "Asia/Shanghai"))
    current = now.astimezone(tz) if now is not None else datetime.now(tz)
    if not _weekday_allowed(job, current):
        return []

    tokens: list[str] = []
    for time_label in _job_time_labels(job):
        hour, minute = _parse_time(time_label)
        scheduled_at = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if current < scheduled_at:
            continue

        late_grace = _safe_int(job.get("late_grace_seconds"), default=7200)
        if late_grace > 0 and (current - scheduled_at).total_seconds() > late_grace:
            continue

        dedupe = str(job.get("dedupe") or "daily").strip().casefold()
        if dedupe == "none":
            token = f"{current.isoformat()}@{time_label}"
        else:
            token = f"{current.date().isoformat()}@{time_label}"
        tokens.append(token)

    return tokens


def job_trigger(job: dict[str, Any]) -> str:
    trigger = str(job.get("trigger") or "schedule").strip().casefold()
    return trigger if trigger in _TRIGGERS else "schedule"


async def run_due_jobs(bot, *, now: datetime | None = None) -> list[PushRunResult]:
    cfg = get_config()
    if not bool(cfg.get("enabled", True)):
        return []

    results: list[PushRunResult] = []
    jobs = cfg.get("jobs") if isinstance(cfg.get("jobs"), list) else []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        for token in due_job_tokens(job, now=now):
            results.append(await run_job(bot, job, token=token, mark_state=True, now=now))
    return results


async def run_event_jobs(bot, trigger: str, *, now: datetime | None = None) -> list[PushRunResult]:
    event_trigger = str(trigger or "").strip().casefold()
    if event_trigger not in {"startup", "shutdown"}:
        return []

    cfg = get_config()
    if not bool(cfg.get("enabled", True)):
        return []

    current = now or datetime.now(_timezone("Asia/Shanghai"))
    token = f"{event_trigger}:{current.isoformat()}"
    results: list[PushRunResult] = []
    jobs = cfg.get("jobs") if isinstance(cfg.get("jobs"), list) else []
    for job in jobs:
        if not isinstance(job, dict) or not bool(job.get("enabled", True)):
            continue
        if job_trigger(job) != event_trigger:
            continue
        results.append(await run_job(bot, job, token=token, mark_state=False, now=current, force=True))
    return results


async def run_job_by_id(bot, job_id: str, *, mark_state: bool = False, force: bool = True) -> PushRunResult | None:
    job = find_job_by_id(job_id)
    if job is None:
        return None
    now = datetime.now(_timezone(str(job.get("timezone") or "Asia/Shanghai")))
    token = f"manual:{now.isoformat()}"
    return await run_job(bot, job, token=token, mark_state=mark_state, now=now, force=force)


async def run_job(
    bot,
    job: dict[str, Any],
    *,
    token: str,
    mark_state: bool,
    now: datetime | None = None,
    force: bool = False,
) -> PushRunResult:
    job_id = str(job.get("id") or "").strip() or "unnamed"
    source_name = str(job.get("source") or "").strip()
    result = PushRunResult(job_id=job_id, source=source_name)

    if get_push_source(source_name) is None:
        result.failed += 1
        result.errors.append(f"未注册消息源: {source_name or '<empty>'}")
        logger.warning("[PushFramework] 任务 %s 使用了未注册消息源: %s", job_id, source_name)
        return result

    targets = normalize_targets(job)
    if not targets:
        result.skipped += 1
        logger.info("[PushFramework] 任务 %s 未配置推送目标，跳过", job_id)
        return result

    cfg = get_config()
    current = now or datetime.now(_timezone(str(job.get("timezone") or "Asia/Shanghai")))
    options = job.get("source_options") if isinstance(job.get("source_options"), dict) else {}

    for target in targets:
        if mark_state and not force and was_sent(job_id, target, token):
            result.skipped += 1
            continue

        result.attempted += 1
        context = PushContext(
            bot=bot,
            job_id=job_id,
            source=source_name,
            target=target,
            options=dict(options),
            now=current,
            force=force,
        )

        try:
            messages = await build_push_messages(source_name, context)
            if not messages:
                result.empty += 1
                if mark_state:
                    mark_sent(job_id, target, token, sent_at=current)
                continue

            for index, push_message in enumerate(messages, start=1):
                label = push_message.label or f"推送消息 {index}"
                await _send_with_retry(
                    lambda message=push_message.message: _send_to_target(bot, target, message),
                    cfg,
                    f"{target.label} {label}",
                    message=push_message.message,
                )
            result.sent += 1
            if mark_state:
                mark_sent(job_id, target, token, sent_at=current)
            logger.info("[PushFramework] 任务 %s 已推送到 %s", job_id, target.label)
        except Exception as e:
            result.failed += 1
            result.errors.append(f"{target.label}: {e}")
            logger.warning("[PushFramework] 任务 %s 推送到 %s 失败: %s", job_id, target.label, e)

    return result


async def _send_to_target(bot, target: PushTarget, message) -> Any:
    if target.kind == "group":
        return await bot.send_group_msg(group_id=target.target_id, message=message)
    return await bot.send_private_msg(user_id=target.target_id, message=message)


async def _send_with_retry(action, cfg: dict[str, Any], label: str, *, message: Any = None) -> Any:
    attempts = max(1, _safe_int(cfg.get("send_retry_attempts"), default=2))
    delay = max(0.0, _safe_float(cfg.get("send_retry_delay_seconds"), default=2.0))
    for attempt in range(1, attempts + 1):
        try:
            return await action()
        except ActionFailed as e:
            if _message_has_media(message) and _is_ambiguous_media_timeout(e):
                logger.warning("[PushFramework] %s 返回媒体发送超时，可能已送达；跳过重试以避免重复发送: %s", label, e)
                return None
            if attempt >= attempts:
                raise
            logger.warning("[PushFramework] %s 发送失败，%.1fs 后重试 %d/%d", label, delay, attempt, attempts - 1)
            await asyncio.sleep(delay)


def _message_has_media(message: Any) -> bool:
    try:
        for segment in message:
            if getattr(segment, "type", "") in _MEDIA_SEGMENT_TYPES:
                return True
    except TypeError:
        pass
    text = str(message or "")
    return any(f"[CQ:{segment_type}" in text for segment_type in _MEDIA_SEGMENT_TYPES)


def _is_ambiguous_media_timeout(error: ActionFailed) -> bool:
    info = getattr(error, "info", {})
    retcode = info.get("retcode") if isinstance(info, dict) else getattr(error, "retcode", None)
    text = "\n".join(
        str(value or "")
        for value in (
            info.get("message") if isinstance(info, dict) else getattr(error, "message", ""),
            info.get("wording") if isinstance(info, dict) else getattr(error, "wording", ""),
            str(error),
        )
    )
    if "rich media transfer failed" in text:
        return False
    return retcode == 1200 and "Timeout" in text


def _job_time_labels(job: dict[str, Any]) -> list[str]:
    raw_times = job.get("times")
    if isinstance(raw_times, list):
        labels = [str(item).strip() for item in raw_times if str(item).strip()]
    else:
        labels = []
    if not labels:
        labels = [str(job.get("time") or "09:00").strip()]
    return labels


def _weekday_allowed(job: dict[str, Any], now: datetime) -> bool:
    raw_days = job.get("days")
    if not isinstance(raw_days, list) or not raw_days:
        return True
    allowed = {_WEEKDAY_ALIASES.get(str(day).strip().casefold()) for day in raw_days}
    allowed.discard(None)
    return now.weekday() in allowed


def _timezone(name: str) -> tzinfo:
    normalized = name.strip()
    if normalized.casefold() == "utc":
        return timezone.utc
    if normalized in _FIXED_TIMEZONES:
        return _FIXED_TIMEZONES[normalized]
    try:
        return ZoneInfo(normalized)
    except ZoneInfoNotFoundError:
        logger.warning("[PushFramework] 未知或不可用时区 %r，回退到 UTC+08:00", name)
        return timezone(timedelta(hours=8), "Asia/Shanghai")


def _parse_time(value: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = value.strip().split(":", maxsplit=1)
        hour = max(0, min(int(hour_text), 23))
        minute = max(0, min(int(minute_text), 59))
        return hour, minute
    except Exception:
        return 9, 0


def _normalize_ids(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    result: list[int] = []
    seen: set[int] = set()
    for item in value:
        try:
            target_id = int(item)
        except (TypeError, ValueError):
            continue
        if target_id <= 0 or target_id in seen:
            continue
        result.append(target_id)
        seen.add(target_id)
    return result


def _safe_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
