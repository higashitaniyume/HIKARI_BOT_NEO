from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import nonebot
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command

from .api import DealMode, SteamDealsClient, SteamDealsError
from .config import get_config
from .render import render_report
from .storage import mark_sent, was_sent_today

logger = logging.getLogger("HikariBot.SteamDeals")

_schedule_task: asyncio.Task[None] | None = None


def _enabled() -> bool:
    return bool(get_config().get("enabled", True))


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        logger.warning("[SteamDeals] 未知时区 %r，回退到 Asia/Shanghai", name)
        return ZoneInfo("Asia/Shanghai")


def _parse_mode(ctx: CommandContext) -> tuple[DealMode, bool, bool]:
    text = " ".join(part for part in [ctx.matched, ctx.args] if part).strip().casefold()
    force_refresh = any(word in text for word in ("刷新", "refresh", "reload"))
    help_requested = any(word in text.split() for word in ("帮助", "help", "菜单"))
    if "免费" in text or "喜加一" in text:
        return "free", force_refresh, help_requested
    if "低价" in text or "特惠" in text:
        return "low", force_refresh, help_requested
    return "all", force_refresh, help_requested


async def _build_report(mode: DealMode, *, force_refresh: bool = False) -> tuple[Path, list[str]]:
    cfg = get_config()
    client = SteamDealsClient(cfg)
    deals = client.filter_deals(await client.fetch_deals(force_refresh=force_refresh), mode)
    path = await render_report(deals, mode=mode, config=cfg)
    links = [deal.url for deal in deals[:5]]
    return path, links


async def _send_report_to_context(ctx: CommandContext, mode: DealMode, *, force_refresh: bool = False) -> None:
    if not _enabled():
        await ctx.send(Message(msg("steam_deals.disabled")))
        return
    await ctx.send(Message(msg("steam_deals.fetching")))
    try:
        path, links = await _build_report(mode, force_refresh=force_refresh)
    except SteamDealsError as e:
        logger.warning("[SteamDeals] 查询失败: %s", e)
        await ctx.send(Message(msg("steam_deals.failed", error=e)))
        return
    await ctx.send(Message(MessageSegment.image(path.resolve().as_uri())))
    if links:
        await ctx.send(Message(msg("steam_deals.links", links="\n".join(links))))


@command(
    "steam日报",
    aliases=("steam", "Steam日报", "steam喜加一", "steam免费", "steam低价", "喜加一", "steam特惠"),
    description="查询 Steam 免费和低价游戏日报",
    usage="steam日报 [免费|低价|刷新]",
    detail_key="steam_deals.help",
)
async def handle_steam_deals(ctx: CommandContext) -> None:
    mode, force_refresh, help_requested = _parse_mode(ctx)
    if help_requested:
        await ctx.send(Message(msg("steam_deals.help")))
        return
    await _send_report_to_context(ctx, mode, force_refresh=force_refresh)


async def _send_daily_push(bot) -> None:
    cfg = get_config()
    whitelist = cfg.get("push_whitelist") or {}
    group_ids = _normalize_ids(whitelist.get("group_ids"))
    private_user_ids = _normalize_ids(whitelist.get("private_user_ids"))
    if not group_ids and not private_user_ids:
        return

    schedule_cfg = cfg.get("schedule") or {}
    tz = _timezone(str(schedule_cfg.get("timezone") or "Asia/Shanghai"))
    today = datetime.now(tz).date().isoformat()
    pending_groups = [target for target in group_ids if not was_sent_today("group", target, today)]
    pending_privates = [target for target in private_user_ids if not was_sent_today("private", target, today)]
    if not pending_groups and not pending_privates:
        return

    try:
        path, links = await _build_report("all", force_refresh=False)
    except Exception as e:
        logger.exception("[SteamDeals] 每日推送生成失败: %s", e)
        return

    image_message = Message(MessageSegment.image(path.resolve().as_uri()))
    link_message = Message(msg("steam_deals.links", links="\n".join(links))) if links else None

    for group_id in pending_groups:
        try:
            await bot.send_group_msg(group_id=group_id, message=image_message)
            if link_message is not None:
                await bot.send_group_msg(group_id=group_id, message=link_message)
            mark_sent("group", group_id, today)
            logger.info("[SteamDeals] 已推送日报到群 %s", group_id)
        except Exception as e:
            logger.warning("[SteamDeals] 推送到群 %s 失败: %s", group_id, e)

    for user_id in pending_privates:
        try:
            await bot.send_private_msg(user_id=user_id, message=image_message)
            if link_message is not None:
                await bot.send_private_msg(user_id=user_id, message=link_message)
            mark_sent("private", user_id, today)
            logger.info("[SteamDeals] 已推送日报到私聊 %s", user_id)
        except Exception as e:
            logger.warning("[SteamDeals] 推送到私聊 %s 失败: %s", user_id, e)


async def _schedule_loop() -> None:
    cfg = get_config()
    schedule_cfg = cfg.get("schedule") or {}
    await asyncio.sleep(max(0, int(schedule_cfg.get("startup_delay_seconds") or 30)))
    while True:
        try:
            cfg = get_config()
            schedule_cfg = cfg.get("schedule") or {}
            if _enabled() and schedule_cfg.get("enabled", False) and _is_due(schedule_cfg):
                bots = nonebot.get_bots()
                if bots:
                    await _send_daily_push(next(iter(bots.values())))
            interval = max(30, int(schedule_cfg.get("check_interval_seconds") or 60))
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("[SteamDeals] 定时循环异常: %s", e)
            await asyncio.sleep(60)


def _is_due(schedule_cfg: dict) -> bool:
    tz = _timezone(str(schedule_cfg.get("timezone") or "Asia/Shanghai"))
    now = datetime.now(tz)
    hour, minute = _parse_time(str(schedule_cfg.get("time") or "10:00"))
    return (now.hour, now.minute) >= (hour, minute)


def _parse_time(value: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = value.strip().split(":", maxsplit=1)
        hour = max(0, min(int(hour_text), 23))
        minute = max(0, min(int(minute_text), 59))
        return hour, minute
    except Exception:
        return 10, 0


def _normalize_ids(value) -> list[int]:
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for item in value:
        try:
            target = int(item)
        except (TypeError, ValueError):
            continue
        if target > 0:
            result.append(target)
    return result


try:
    driver = nonebot.get_driver()
except ValueError:
    driver = None

if driver is not None:

    @driver.on_startup
    async def _start_scheduler() -> None:
        global _schedule_task
        if _schedule_task is None or _schedule_task.done():
            _schedule_task = asyncio.create_task(_schedule_loop())

    @driver.on_shutdown
    async def _stop_scheduler() -> None:
        if _schedule_task is None:
            return
        _schedule_task.cancel()
        try:
            await _schedule_task
        except asyncio.CancelledError:
            pass
