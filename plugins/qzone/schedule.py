"""QQ 空间定时说说。

按 `plugin_configs/qzone.json` 里的 `schedule` 段轮询：到点用 AI（或备用文案）
生成一条人设短文，再调用 `service.publish` 发到空间。

设计约束：
- 默认关闭（`schedule.enabled=false`），打开后才会自动发；
- 每条任务每天最多发一次，重启不会重复，状态写在 `UserData/qzone_state.json`；
- 失败只记日志并退避重试（15 分钟），连续失败 3 次当天放弃，不在群里刷错误。
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import random
import re
import time
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import nonebot

from . import service
from .config import VISIBLE_VALUES, get_config

logger = logging.getLogger("HikariBot.QZone.Schedule")

_WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
_FIXED_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
_RETRY_DELAY_SECONDS = 900
_MAX_FAILURES_PER_DAY = 3
_STARTUP_DELAY_SECONDS = 20

_state_lock = asyncio.Lock()
_tick_lock = asyncio.Lock()
_task: asyncio.Task[None] | None = None
_retry_after: dict[str, float] = {}
_fail_counts: dict[str, int] = {}


# ── 状态文件 ──────────────────────────────────────────────


def _state_path(schedule_cfg: dict[str, Any]) -> Path:
    return Path(str(schedule_cfg.get("state_path") or "UserData/qzone_state.json"))


def load_state(path: Path | None = None) -> dict[str, Any]:
    target = path or _state_path(get_config()["schedule"])
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"jobs": {}}
    except OSError as exc:
        logger.warning("[QZone] 读取定时状态失败 %s: %s", target, exc)
        return {"jobs": {}}
    try:
        data = json.loads(raw)
    except ValueError:
        logger.warning("[QZone] 定时状态文件不是合法 JSON，已忽略: %s", target)
        return {"jobs": {}}
    jobs = data.get("jobs")
    return {"jobs": jobs if isinstance(jobs, dict) else {}}


def save_state(state: dict[str, Any], path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        logger.warning("[QZone] 写入定时状态失败 %s: %s", path, exc)


def _record_sent(
    job: dict[str, Any],
    text: str,
    tid: str,
    schedule_cfg: dict[str, Any],
    *,
    now: datetime | None = None,
) -> None:
    path = _state_path(schedule_cfg)
    state = load_state(path)
    jobs = state.setdefault("jobs", {})
    current = now or datetime.now(_job_tz(job))
    jobs[job["id"]] = {
        "date": current.date().isoformat(),
        "at": current.strftime("%Y-%m-%d %H:%M"),
        "tid": tid,
        "text": text[:200],
    }
    save_state(state, path)


# ── 时间计算 ──────────────────────────────────────────────


def _job_tz(job: dict[str, Any]) -> tzinfo:
    name = str(job.get("timezone") or "Asia/Shanghai")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return _FIXED_TZ


def job_due(
    job: dict[str, Any],
    state: dict[str, Any],
    *,
    now: datetime | None = None,
    grace_seconds: int = 1800,
) -> bool:
    if not job.get("enabled", True):
        return False
    tz = _job_tz(job)
    current = now or datetime.now(tz)
    if job.get("days") and current.weekday() not in job["days"]:
        return False
    target = current.replace(hour=int(job["hour"]), minute=int(job["minute"]), second=0, microsecond=0)
    if current < target:
        return False
    if (current - target).total_seconds() > max(0, int(grace_seconds)):
        return False
    last = state.get("jobs", {}).get(job["id"], {})
    return str(last.get("date") or "") != current.date().isoformat()


def next_run(job: dict[str, Any], *, now: datetime | None = None) -> str:
    tz = _job_tz(job)
    current = now or datetime.now(tz)
    for offset in range(0, 8):
        candidate_date = current.date() + timedelta(days=offset)
        if job.get("days") and candidate_date.weekday() not in job["days"]:
            continue
        candidate = datetime(
            candidate_date.year,
            candidate_date.month,
            candidate_date.day,
            int(job["hour"]),
            int(job["minute"]),
            tzinfo=tz,
        )
        if candidate > current:
            return candidate.strftime("%m-%d %H:%M")
    return "-"


# ── 文案生成 ──────────────────────────────────────────────


async def compose_text(
    job: dict[str, Any],
    schedule_cfg: dict[str, Any],
    *,
    now: datetime | None = None,
) -> tuple[str, str]:
    """返回 (正文, 来源)，来源为 "ai" 或 "fallback"。"""
    ai_cfg = schedule_cfg["ai"]
    if ai_cfg["enabled"]:
        try:
            text = await _generate_with_ai(job, ai_cfg, schedule_cfg, now=now)
            if text:
                return text, "ai"
        except Exception as exc:
            logger.warning("[QZone] 定时文案 AI 生成失败 job=%s: %s", job["id"], exc)

    fallback = schedule_cfg.get("fallback_texts") or []
    if fallback:
        return random.choice(fallback), "fallback"
    raise service.QzoneActionError("定时文案为空：AI 生成失败且未配置 fallback_texts")


async def _generate_with_ai(
    job: dict[str, Any],
    ai_cfg: dict[str, Any],
    schedule_cfg: dict[str, Any],
    *,
    now: datetime | None = None,
) -> str:
    from plugins.aiagent.client import post_chat_completion
    from plugins.aiagent.config import get_config as get_aiagent_config

    request_cfg = copy.deepcopy(get_aiagent_config())
    model_cfg = dict(request_cfg.get("model") or {})
    model_cfg["temperature"] = ai_cfg["temperature"]
    model_cfg["max_tokens"] = ai_cfg["max_tokens"]
    model_cfg["timeout_seconds"] = ai_cfg["timeout_seconds"]
    request_cfg["model"] = model_cfg
    request_cfg["tools"] = {"max_tool_rounds": 0}
    # 短文案不需要思考模式：开了会白烧 token，长思考还可能把 max_tokens 用光导致空回复
    request_cfg["thinking"] = {"enabled": False}

    messages = [
        {
            "role": "system",
            "content": (
                f"{ai_cfg['persona']}\n"
                "你在给 QQ 空间写一条说说：只输出正文本身，不要引号、不要话题标签、"
                "不要链接、不要 Markdown，也不要解释你在做什么。"
            ),
        },
        {"role": "user", "content": _build_prompt(job, ai_cfg, schedule_cfg, now=now)},
    ]
    message = await post_chat_completion(request_cfg, messages, tools=[], caller="qzone")
    return _clean_generated(str(message.get("content") or ""), ai_cfg["max_chars"])


def _build_prompt(
    job: dict[str, Any],
    ai_cfg: dict[str, Any],
    schedule_cfg: dict[str, Any],
    *,
    now: datetime | None = None,
) -> str:
    current = now or datetime.now(_job_tz(job))
    recent = _recent_texts(schedule_cfg)
    low = max(20, ai_cfg["max_chars"] - 40)
    high = max(low + 10, ai_cfg["max_chars"] - 10)
    lines = [
        f"现在时间：{current.strftime('%Y-%m-%d %H:%M')}（{_WEEKDAY_CN[current.weekday()]}）",
        f"这条说说想表达：{job.get('prompt') or '随便聊聊现在的心情'}",
        f"要求：第一人称口语，{low}~{high} 字，"
        "只写一段，最多一个括号小动作，不要每句都用同样的口头禅。",
    ]
    if recent:
        lines.append("最近已经发过这些，别写重复的：" + "；".join(recent))
    return "\n".join(lines)


def _recent_texts(schedule_cfg: dict[str, Any], limit: int = 3) -> list[str]:
    state = load_state(_state_path(schedule_cfg))
    entries = [entry for entry in state.get("jobs", {}).values() if isinstance(entry, dict)]
    entries.sort(key=lambda entry: str(entry.get("at") or ""), reverse=True)
    texts = [str(entry.get("text") or "").strip() for entry in entries[:limit]]
    return [text for text in texts if text]


_QUOTE_CHARS = "\"'“”‘’「」『』"
_MARKDOWN_NOISE_RE = re.compile(r"(\*\*|__|`|#+\s*|>\s*)")


def _clean_generated(text: str, max_chars: int) -> str:
    value = " ".join(line.strip() for line in str(text or "").strip().splitlines() if line.strip())
    if not value:
        return ""
    value = _MARKDOWN_NOISE_RE.sub("", value).strip()
    value = value.strip(_QUOTE_CHARS).strip()
    value = re.sub(r"\s+", " ", value)
    if len(value) > max_chars:
        value = value[: max_chars - 1].rstrip() + "…"
    return value


# ── 执行 ──────────────────────────────────────────────────


def _pick_bot() -> Any | None:
    try:
        bots = nonebot.get_bots()
    except Exception:  # pragma: no cover - 驱动未初始化
        return None
    return next(iter(bots.values()), None) if bots else None


def find_job(job_id: str) -> dict[str, Any] | None:
    target = str(job_id or "").strip()
    if not target:
        return None
    for job in get_config()["schedule"]["jobs"]:
        if job["id"] == target:
            return job
    return None


async def run_job(
    job: dict[str, Any],
    *,
    cfg: dict[str, Any] | None = None,
    manual: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    config = cfg or get_config()
    schedule_cfg = config["schedule"]
    bot = _pick_bot()
    if bot is None:
        raise RuntimeError("当前没有在线的 OneBot 连接，跳过定时说说")

    text, source = await compose_text(job, schedule_cfg, now=now)
    result = await service.publish(
        bot,
        text,
        visible=VISIBLE_VALUES.get(job["visible"], 1),
        timeout=config["timeout"],
    )
    tid = str(result.get("tid") or "")
    _record_sent(job, text, tid, schedule_cfg, now=now)
    logger.info(
        "[QZone] 定时说说已发布 job=%s source=%s manual=%s tid=%s len=%d",
        job["id"],
        source,
        manual,
        tid or "-",
        len(text),
    )
    return {"job_id": job["id"], "tid": tid, "text": text, "source": source}


async def tick(*, now: datetime | None = None) -> list[dict[str, Any]]:
    """扫描一次到期任务，返回本次实际发布的结果。"""
    async with _tick_lock:
        cfg = get_config()
        schedule_cfg = cfg["schedule"]
        if not cfg["enabled"] or not schedule_cfg["enabled"]:
            return []
        state = load_state(_state_path(schedule_cfg))
        published: list[dict[str, Any]] = []
        for job in schedule_cfg["jobs"]:
            if not job_due(job, state, now=now, grace_seconds=int(schedule_cfg["grace_seconds"])):
                continue
            if _retry_after.get(job["id"], 0.0) > time.monotonic():
                continue
            try:
                published.append(await run_job(job, cfg=cfg, now=now))
                _retry_after.pop(job["id"], None)
                _fail_counts.pop(job["id"], None)
            except Exception as exc:
                failures = _fail_counts.get(job["id"], 0) + 1
                _fail_counts[job["id"]] = failures
                _retry_after[job["id"]] = time.monotonic() + _RETRY_DELAY_SECONDS
                logger.warning(
                    "[QZone] 定时说说失败 job=%s failures=%d: %s", job["id"], failures, exc
                )
                if failures >= _MAX_FAILURES_PER_DAY:
                    _mark_failed_today(job, schedule_cfg)
                    _fail_counts.pop(job["id"], None)
                    _retry_after.pop(job["id"], None)
        return published


def _mark_failed_today(job: dict[str, Any], schedule_cfg: dict[str, Any]) -> None:
    """当天连续失败太多次，先记成已完成，避免一直重试打扰。"""
    path = _state_path(schedule_cfg)
    state = load_state(path)
    now = datetime.now(_job_tz(job))
    state.setdefault("jobs", {})[job["id"]] = {
        "date": now.date().isoformat(),
        "at": now.strftime("%Y-%m-%d %H:%M"),
        "tid": "",
        "text": "",
        "error": "连续失败，当天放弃",
    }
    save_state(state, path)


async def _schedule_loop() -> None:
    await asyncio.sleep(_STARTUP_DELAY_SECONDS)
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - 兜底
            logger.exception("[QZone] 定时循环异常: %s", exc)
        interval = get_config()["schedule"]["check_interval_seconds"]
        await asyncio.sleep(interval)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_schedule_loop(), name="HikariQZoneSchedule")
        logger.info("[QZone] 定时说说循环已启动")


async def stop() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None


# ── 状态展示 ──────────────────────────────────────────────


def status_snapshot() -> dict[str, Any]:
    cfg = get_config()
    schedule_cfg = cfg["schedule"]
    state = load_state(_state_path(schedule_cfg))
    jobs: list[dict[str, Any]] = []
    for job in schedule_cfg["jobs"]:
        entry = state.get("jobs", {}).get(job["id"], {})
        entry = entry if isinstance(entry, dict) else {}
        jobs.append(
            {
                "id": job["id"],
                "enabled": job["enabled"],
                "time": job["time"],
                "days": job["days"],
                "visible_label": job["visible_label"],
                "next_run": next_run(job),
                "last_date": str(entry.get("date") or ""),
                "last_tid": str(entry.get("tid") or ""),
                "error": str(entry.get("error") or ""),
            }
        )
    return {
        "enabled": bool(schedule_cfg["enabled"]),
        "ai_enabled": bool(schedule_cfg["ai"]["enabled"]),
        "jobs": jobs,
        "state_path": str(_state_path(schedule_cfg)),
    }


try:
    _driver = nonebot.get_driver()
except ValueError:  # pragma: no cover - 未初始化 NoneBot（单测/脚本）
    _driver = None

if _driver is not None:

    @_driver.on_startup
    async def _start_schedule() -> None:
        start()

    @_driver.on_shutdown
    async def _stop_schedule() -> None:
        await stop()
