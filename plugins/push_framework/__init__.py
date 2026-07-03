"""Generic scheduled push framework plugin."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

import nonebot
from nonebot.adapters.onebot.v11 import Message

from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command
from core.config_loader import load_main_config

from .config import get_config
from .registry import (
    PushContext,
    PushMessage,
    PushSource,
    PushTarget,
    get_push_source,
    iter_push_sources,
    register_push_source,
)
from .scheduler import PushRunResult, job_trigger, run_due_jobs, run_event_jobs, run_job_by_id

logger = logging.getLogger("HikariBot.PushFramework")

_schedule_task: asyncio.Task[None] | None = None
_runner_loop: asyncio.AbstractEventLoop | None = None


def _static_text_source(ctx: PushContext) -> list[str]:
    text = ctx.options.get("text", "")
    if isinstance(text, list):
        text = "\n".join(str(item) for item in text)
    text = str(text or "").strip()
    return [text] if text else []


register_push_source(
    "static_text",
    _static_text_source,
    description="发送 source_options.text 中的固定文本，用于测试推送链路。",
)


async def _schedule_loop() -> None:
    cfg = get_config()
    await asyncio.sleep(max(0, _safe_int(cfg.get("startup_delay_seconds"), default=15)))
    try:
        bots = nonebot.get_bots()
        if bots and bool(get_config().get("enabled", True)):
            await run_event_jobs(next(iter(bots.values())), "startup")
    except Exception as e:
        logger.exception("[PushFramework] 启动触发推送异常: %s", e)

    while True:
        try:
            cfg = get_config()
            if bool(cfg.get("enabled", True)):
                bots = nonebot.get_bots()
                if bots:
                    await run_due_jobs(next(iter(bots.values())))
            interval = max(10, _safe_int(cfg.get("check_interval_seconds"), default=60))
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("[PushFramework] 定时循环异常: %s", e)
            await asyncio.sleep(60)


@command(
    "推送",
    aliases=("push", "Push"),
    description="管理定时推送框架",
    usage="推送 [状态|源|触发 <任务ID>]",
    detail_key="push_framework.help",
    require_tome=True,
    show_in_help=False,
)
async def handle_push_framework(ctx: CommandContext) -> None:
    if not _is_superuser(ctx):
        await ctx.send(Message(msg("push_framework.permission_denied")))
        return

    args = ctx.args.strip()
    if not args or args.casefold() in {"帮助", "help", "菜单"}:
        await ctx.send(Message(msg("push_framework.help")))
        return

    action, _, rest = args.partition(" ")
    normalized_action = action.strip().casefold()
    if normalized_action in {"状态", "status"}:
        await ctx.send(Message(_format_status()))
        return

    if normalized_action in {"源", "sources", "source"}:
        await ctx.send(Message(_format_sources()))
        return

    if normalized_action in {"测试", "test", "执行", "run", "触发", "手动", "立即", "send"}:
        job_id = rest.strip()
        if not job_id:
            await ctx.send(Message(msg("push_framework.test_usage")))
            return
        result = await run_job_by_id(ctx.bot, job_id, mark_state=False, force=True)
        if result is None:
            await ctx.send(Message(msg("push_framework.job_not_found", job_id=job_id)))
            return
        await ctx.send(Message(_format_run_result(result)))
        return

    await ctx.send(Message(msg("push_framework.help")))


def _format_status() -> str:
    cfg = get_config()
    sources = iter_push_sources()
    jobs = cfg.get("jobs") if isinstance(cfg.get("jobs"), list) else []
    lines = [
        msg("push_framework.status_header", enabled=_enabled_label(bool(cfg.get("enabled", True)))),
        msg("push_framework.status_sources", count=len(sources)),
        msg("push_framework.status_jobs", count=len(jobs)),
    ]
    for job in jobs:
        if not isinstance(job, dict):
            continue
        job_id = str(job.get("id") or "").strip() or "<unnamed>"
        source_name = str(job.get("source") or "").strip() or "<empty>"
        enabled = _enabled_label(bool(job.get("enabled", True)))
        target_count = _target_count(job)
        time_text = _format_job_time(job)
        trigger = job_trigger(job)
        source_state = "已注册" if get_push_source(source_name) is not None else "未注册"
        lines.append(
            msg(
                "push_framework.status_job_line",
                job_id=job_id,
                enabled=enabled,
                trigger=trigger,
                source=source_name,
                time=time_text,
                targets=target_count,
                state=source_state,
            )
        )
    return "\n".join(lines)


def _format_sources() -> str:
    sources = iter_push_sources()
    if not sources:
        return msg("push_framework.source_empty")
    lines = [msg("push_framework.source_header")]
    for source in sources:
        description = f" - {source.description}" if source.description else ""
        lines.append(msg("push_framework.source_line", name=source.name, description=description))
    return "\n".join(lines)


def _format_run_result(result: PushRunResult) -> str:
    lines = [
        msg(
            "push_framework.run_result",
            job_id=result.job_id,
            attempted=result.attempted,
            sent=result.sent,
            skipped=result.skipped,
            empty=result.empty,
            failed=result.failed,
        )
    ]
    if result.errors:
        lines.append(msg("push_framework.run_errors", errors="\n".join(result.errors[:5])))
    return "\n".join(lines)


def _format_job_time(job: dict[str, Any]) -> str:
    times = job.get("times")
    if isinstance(times, list) and times:
        return ",".join(str(item) for item in times)
    return str(job.get("time") or "09:00")


def _target_count(job: dict[str, Any]) -> int:
    targets = job.get("targets") if isinstance(job.get("targets"), dict) else {}
    group_ids = targets.get("group_ids") if isinstance(targets.get("group_ids"), list) else []
    private_ids = targets.get("private_user_ids") if isinstance(targets.get("private_user_ids"), list) else []
    return len(group_ids) + len(private_ids)


def _enabled_label(enabled: bool) -> str:
    return "开启" if enabled else "关闭"


def _is_superuser(ctx: CommandContext) -> bool:
    try:
        superuser_id = str(load_main_config().get("bot", {}).get("superuser_id") or "").strip()
        return bool(superuser_id) and str(ctx.event.get_user_id()).strip() == superuser_id
    except Exception:
        return False


def _safe_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


async def _run_manual_job(job_id: str) -> PushRunResult | None:
    bots = nonebot.get_bots()
    if not bots:
        raise RuntimeError("当前没有可用 Bot 连接，无法手动触发推送。")
    return await run_job_by_id(next(iter(bots.values())), job_id, mark_state=False, force=True)


def submit_manual_push(job_id: str, *, timeout_seconds: float = 300.0) -> PushRunResult | None:
    """Run one push job from a non-async management thread."""

    loop = _runner_loop
    if loop is None or loop.is_closed():
        raise RuntimeError("推送框架尚未就绪，稍后再试。")
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None
    if running_loop is loop:
        raise RuntimeError("当前上下文不能同步触发推送。")

    future = asyncio.run_coroutine_threadsafe(_run_manual_job(job_id), loop)
    try:
        return future.result(timeout=max(1.0, float(timeout_seconds)))
    except FutureTimeoutError as e:
        future.cancel()
        raise TimeoutError("手动推送执行超时。") from e


try:
    driver = nonebot.get_driver()
except ValueError:
    driver = None

if driver is not None:

    @driver.on_startup
    async def _start_scheduler() -> None:
        global _runner_loop, _schedule_task
        _runner_loop = asyncio.get_running_loop()
        if _schedule_task is None or _schedule_task.done():
            _schedule_task = asyncio.create_task(_schedule_loop(), name="HikariPushFramework")

    @driver.on_shutdown
    async def _stop_scheduler() -> None:
        try:
            bots = nonebot.get_bots()
            if bots and bool(get_config().get("enabled", True)):
                await run_event_jobs(next(iter(bots.values())), "shutdown")
        except Exception as e:
            logger.exception("[PushFramework] 关闭触发推送异常: %s", e)

        if _schedule_task is None:
            return
        _schedule_task.cancel()
        try:
            await _schedule_task
        except asyncio.CancelledError:
            pass


__all__ = [
    "PushContext",
    "PushMessage",
    "PushSource",
    "PushTarget",
    "register_push_source",
    "submit_manual_push",
]
