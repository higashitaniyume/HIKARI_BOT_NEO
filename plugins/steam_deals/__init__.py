from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import nonebot
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed

from core.ai_tool_registry import AIToolContext, register_ai_tool
from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command
from plugins.push_framework.registry import PushContext, PushMessage, register_push_source

from .api import DealMode, SteamDealsClient, SteamDealsError
from .config import get_config
from .render import render_report
from .storage import mark_sent, was_sent_today

logger = logging.getLogger("HikariBot.SteamDeals")

_schedule_task: asyncio.Task[None] | None = None
T = TypeVar("T")


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


def _is_send_timeout(error: ActionFailed) -> bool:
    text = f"{getattr(error, 'message', '')}\n{getattr(error, 'wording', '')}"
    return getattr(error, "retcode", None) == 1200 or "Timeout" in text


async def _send_with_retry(action: Callable[[], Awaitable[T]], label: str) -> T:
    cfg = get_config()
    attempts = max(1, int(cfg.get("send_retry_attempts") or 2))
    delay = max(0.0, float(cfg.get("send_retry_delay_seconds") or 2.0))
    last_error: ActionFailed | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await action()
        except ActionFailed as e:
            last_error = e
            if not _is_send_timeout(e) or attempt >= attempts:
                raise
            logger.warning("[SteamDeals] %s 发送超时，%.1fs 后重试 %d/%d: %s", label, delay, attempt, attempts - 1, e)
            await asyncio.sleep(delay)
    assert last_error is not None
    raise last_error


async def _send_image_to_context(ctx: CommandContext, path: Path, links: list[str]) -> bool:
    uri = path.resolve().as_uri()
    try:
        await _send_with_retry(lambda: ctx.send(Message(MessageSegment.image(uri))), "日报图片")
        return True
    except ActionFailed as e:
        if not _is_send_timeout(e):
            raise
        logger.warning("[SteamDeals] 日报图片发送超时，改发文本兜底: %s", e)
        await _try_send_context_text(ctx, msg("steam_deals.image_send_failed", links="\n".join(links) or "暂无链接"))
        return False


async def _try_send_context_text(ctx: CommandContext, text: str) -> None:
    try:
        await _send_with_retry(lambda: ctx.send(Message(text)), "日报文本")
    except Exception as e:
        logger.warning("[SteamDeals] 日报文本发送失败: %s", e)


async def _send_report_to_context(ctx: CommandContext, mode: DealMode, *, force_refresh: bool = False) -> None:
    if not _enabled():
        await ctx.send(Message(msg("steam_deals.disabled")))
        return
    try:
        path, links = await _build_report(mode, force_refresh=force_refresh)
    except SteamDealsError as e:
        logger.warning("[SteamDeals] 查询失败: %s", e)
        await ctx.send(Message(msg("steam_deals.failed", error=e)))
        return
    image_sent = await _send_image_to_context(ctx, path, links)
    if image_sent and links:
        await _try_send_context_text(ctx, msg("steam_deals.links", links="\n".join(links)))


async def _steam_deals_push_source(ctx: PushContext) -> list[PushMessage]:
    if not _enabled():
        return []

    mode = _normalize_push_mode(ctx.options.get("mode"))
    force_refresh = _parse_push_bool(ctx.options.get("force_refresh"), default=False)
    include_links = _parse_push_bool(ctx.options.get("include_links"), default=True)
    path, links = await _build_report(mode, force_refresh=force_refresh)
    messages = [
        PushMessage(Message(MessageSegment.image(path.resolve().as_uri())), "Steam 日报图片"),
    ]
    if include_links and links:
        messages.append(PushMessage(Message(msg("steam_deals.links", links="\n".join(links))), "Steam 商店链接"))
    return messages


def _normalize_push_mode(value) -> DealMode:
    mode = str(value or "all").strip().casefold()
    if mode in {"free", "免费", "喜加一"}:
        return "free"
    if mode in {"low", "低价", "特惠"}:
        return "low"
    return "all"


def _parse_push_bool(value, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"1", "true", "yes", "on", "启用", "是"}


@register_ai_tool(
    "steam_deals_list",
    plugin_name="steam_deals",
    description="Fetch Steam deals and return a compact list of free, low-price, or highlighted discounted games.",
    parameters={
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "description": "Deal filter mode: all, free, or low.",
                "enum": ["all", "free", "low"],
            },
            "max_items": {
                "type": "integer",
                "description": "Maximum number of deals to return.",
                "minimum": 1,
                "maximum": 30,
            },
            "force_refresh": {
                "type": "boolean",
                "description": "Whether to bypass the short in-memory cache.",
            },
        },
        "additionalProperties": False,
    },
)
async def ai_tool_steam_deals_list(context: AIToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    if not _enabled():
        return {"error": "steam_deals is disabled"}

    cfg = dict(get_config())
    price_watch = dict(cfg.get("price_watch") or {})
    price_watch["enabled"] = False
    cfg["price_watch"] = price_watch

    mode = _normalize_push_mode(arguments.get("mode"))
    max_items = _tool_int(arguments.get("max_items"), default=int(cfg.get("max_items") or 18), minimum=1, maximum=30)
    cfg["max_items"] = max_items
    force_refresh = _parse_push_bool(arguments.get("force_refresh"), default=False)
    client = SteamDealsClient(cfg)

    try:
        deals = client.filter_deals(await client.fetch_deals(force_refresh=force_refresh), mode)[:max_items]
    except SteamDealsError as e:
        logger.warning("[SteamDeals] AI Tool 查询失败: %s", e)
        return {"mode": mode, "error": str(e)}

    return {
        "mode": mode,
        "count": len(deals),
        "items": [_deal_payload(deal, cfg) for deal in deals],
    }


def _tool_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return min(max(parsed, minimum), maximum)


def _deal_payload(deal, cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "appid": deal.appid,
        "name": deal.name,
        "url": deal.url,
        "discount_percent": deal.discount_percent,
        "original_price_cents": deal.original_price_cents,
        "final_price_cents": deal.final_price_cents,
        "price": _price_text(deal.final_price_cents, cfg),
        "currency": deal.currency,
        "source": deal.source,
        "released": deal.released,
        "review_summary": deal.review_summary,
        "review_percent": deal.review_percent,
        "review_count": deal.review_count,
        "promotion_kind": deal.promotion_kind,
        "promotion_start": deal.promotion_start,
        "promotion_end": deal.promotion_end,
        "categories": sorted(str(item) for item in deal.categories),
    }


def _price_text(cents: int, cfg: dict[str, Any]) -> str:
    if cents <= 0:
        return "免费"
    symbol = str(cfg.get("currency_symbol") or "").strip()
    return f"{symbol}{cents / 100:.2f}" if symbol else f"{cents / 100:.2f}"


register_push_source(
    "steam_deals",
    _steam_deals_push_source,
    description="推送 Steam 热门热卖、免费和低价游戏日报。",
    default_options={
        "mode": "all",
        "include_links": True,
        "force_refresh": False,
    },
)


@command(
    "steam日报",
    aliases=("steam", "Steam日报", "steam喜加一", "steam免费", "steam低价", "喜加一", "steam特惠"),
    description="查询 Steam 热门热卖、免费和低价游戏日报",
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
            await _send_with_retry(lambda: bot.send_group_msg(group_id=group_id, message=image_message), f"群 {group_id} 日报图片")
            if link_message is not None:
                await _send_with_retry(lambda: bot.send_group_msg(group_id=group_id, message=link_message), f"群 {group_id} 日报链接")
            mark_sent("group", group_id, today)
            logger.info("[SteamDeals] 已推送日报到群 %s", group_id)
        except Exception as e:
            logger.warning("[SteamDeals] 推送到群 %s 失败: %s", group_id, e)

    for user_id in pending_privates:
        try:
            await _send_with_retry(lambda: bot.send_private_msg(user_id=user_id, message=image_message), f"私聊 {user_id} 日报图片")
            if link_message is not None:
                await _send_with_retry(lambda: bot.send_private_msg(user_id=user_id, message=link_message), f"私聊 {user_id} 日报链接")
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
