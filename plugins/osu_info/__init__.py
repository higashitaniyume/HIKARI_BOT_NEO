"""osu! 信息查询插件入口 — 命令路由和注册。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment, PrivateMessageEvent

from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command
from core.stats_tracker import increment as stats_increment

from .api import MODE_ALIASES, OsuApiClient, OsuApiError, OsuAuthError, OsuNotFoundError, normalize_mode, split_mode_and_target
from .config import get_config
from .downloader import OsuDownloadError, OsuDownloadNeedsLogin, download_beatmapset_from_official, extract_beatmapset_id, official_download_url, official_page_url
from .render import render_beatmap, render_beatmap_search, render_dashboard, render_notice, render_ranking, render_scores, render_user_card
from .storage import get_binding, remove_binding, set_binding

logger = logging.getLogger("HikariBot.OsuInfo")

_client: OsuApiClient | None = None
_client_key: tuple[str, str, str, str] | None = None


def _cache_dir() -> Path:
    return Path(str(get_config().get("cache_dir") or "/tmp/hikari_bot/osu_info"))


def _proxy() -> str:
    return str(get_config().get("proxy") or "").strip()


def _get_client() -> OsuApiClient:
    global _client, _client_key
    cfg = get_config()
    key = (str(cfg.get("client_id") or ""), str(cfg.get("client_secret") or ""), str(cfg.get("api_base") or ""), str(cfg.get("proxy") or ""))
    if _client is None or _client_key != key:
        _client = OsuApiClient(cfg)
        _client_key = key
    return _client


async def _send_image(ctx: CommandContext, path: Path) -> None:
    await ctx.send(Message(MessageSegment.image(path.resolve().as_uri())))


async def _send_notice(ctx: CommandContext, title: str, lines: list[str]) -> None:
    await _send_image(ctx, await render_notice(title, lines, _cache_dir()))


async def _send_resource_notice(ctx: CommandContext, title_key: str, body_key: str, **kwargs: Any) -> None:
    await _send_notice(ctx, msg(f"osu.{title_key}"), msg(f"osu.{body_key}", **kwargs).splitlines())


def _default_mode() -> str:
    return normalize_mode(str(get_config().get("default_mode") or "osu"))


def _enabled() -> bool:
    return bool(get_config().get("enabled", True))


def _bound_target(ctx, mode, target, *, raw_args):
    if target:
        return mode, target
    binding = get_binding(ctx.event.get_user_id())
    if binding is None:
        return None
    if not raw_args.strip():
        mode = binding.mode
    return mode, str(binding.osu_id)


async def _get_bound_or_named_user(ctx, args):
    mode, target = split_mode_and_target(args, _default_mode())
    resolved = _bound_target(ctx, mode, target, raw_args=args)
    if resolved is None:
        await _send_resource_notice(ctx, "needs_binding_title", "needs_binding")
        return None
    mode, target = resolved
    user = await _get_client().get_user(target, mode)
    return user, mode


async def _get_recent_scores_for_card(user_id, mode):
    try:
        return await _get_client().get_user_scores(user_id, mode, "recent", limit=min(int(get_config().get("score_limit") or 5), 3))
    except OsuApiError as e:
        logger.info("[osu] 最近成绩获取失败，继续渲染用户资料: %s", e)
        return []


# Import command handlers (these reference __init__ functions via parent module lookup)
from .commands import (  # noqa: E402
    handle_osu_beatmap,
    handle_osu_bind,
    handle_osu_dashboard,
    handle_osu_download,
    handle_osu_help,
    handle_osu_ranking,
    handle_osu_scores,
    handle_osu_unbind,
    handle_osu_user,
)
from .ai_tools import (  # noqa: E402, F401 — register AI tools on import
    ai_tool_osu_beatmap_lookup,
    ai_tool_osu_ranking_lookup,
    ai_tool_osu_scores_lookup,
    ai_tool_osu_user_lookup,
)

_SUBCOMMAND_ALIASES = {
    "help": "help", "帮助": "help", "菜单": "help",
    "bind": "bind", "绑定": "bind",
    "unbind": "unbind", "解绑": "unbind",
    "user": "user", "用户": "user", "信息": "user", "profile": "user",
    "看板": "dashboard", "卡片": "dashboard", "card": "dashboard", "dashboard": "dashboard",
    "score": "scores", "scores": "scores", "成绩": "scores", "bp": "scores",
    "ranking": "ranking", "rank": "ranking", "排名": "ranking", "排行榜": "ranking",
    "beatmap": "beatmap", "map": "beatmap", "谱面": "beatmap",
    "download": "download", "dl": "download", "下载": "download",
}


async def _call_with_args(ctx: CommandContext, args: str, handler) -> None:
    old_args = ctx.args
    ctx.args = args
    try:
        await handler(ctx)
    finally:
        ctx.args = old_args


def _split_osu_subcommand(args: str) -> tuple[str | None, str]:
    text = args.strip()
    if not text:
        return None, ""
    parts = text.split(maxsplit=1)
    head = parts[0].casefold()
    subcommand = _SUBCOMMAND_ALIASES.get(head)
    if subcommand is None:
        return None, text
    return subcommand, parts[1].strip() if len(parts) > 1 else ""


@command("osu", description="osu! 信息查询", usage="osu", detail_key="osu.help", require_tome=True)
async def handle_osu(ctx: CommandContext) -> None:
    subcommand, rest = _split_osu_subcommand(ctx.args)
    handlers = {
        "help": handle_osu_help,
        "bind": handle_osu_bind,
        "unbind": handle_osu_unbind,
        "user": handle_osu_user,
        "dashboard": handle_osu_dashboard,
        "scores": handle_osu_scores,
        "ranking": handle_osu_ranking,
        "beatmap": handle_osu_beatmap,
        "download": handle_osu_download,
    }
    handler = handlers.get(subcommand)
    if handler:
        await _call_with_args(ctx, rest, handler)
    else:
        await _call_with_args(ctx, ctx.args, handle_osu_user)


get_config()

# Test compatibility re-exports
from .commands import _extract_beatmap_id, _resolve_download_beatmapset_id, _score_args, _send_download_link, _upload_file  # noqa: E402, F401
