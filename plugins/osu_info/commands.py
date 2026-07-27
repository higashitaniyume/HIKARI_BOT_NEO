"""
osu! 命令 handler 模块。

处理 osu help/bind/unbind/user/dashboard/scores/ranking/beatmap/download 子命令。
使用延迟导入父模块以支持测试 mock 补丁。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment, PrivateMessageEvent

from core.bot_messages import get_message as msg
from core.command_router import CommandContext
from core.stats_tracker import increment as stats_increment

from .api import MODE_ALIASES, OsuApiClient, OsuApiError, OsuAuthError, OsuNotFoundError, normalize_mode, split_mode_and_target
from .config import get_config
from .downloader import OsuDownloadError, OsuDownloadNeedsLogin, download_beatmapset_from_official, extract_beatmapset_id, official_download_url, official_page_url
from .render import render_beatmap, render_beatmap_search, render_dashboard, render_notice, render_ranking, render_scores, render_user_card
from .storage import get_binding, remove_binding, set_binding

logger = logging.getLogger("HikariBot.OsuInfo.Commands")


def _import(name):
    """Lazy import a function from the parent module (supports test mocking)."""
    import plugins.osu_info
    return getattr(plugins.osu_info, name)


def _cache_dir():
    return _import("_cache_dir")()


def _proxy():
    return _import("_proxy")()


async def _send_image(ctx, path):
    await _import("_send_image")(ctx, path)


async def _send_notice(ctx, title, lines):
    await _import("_send_notice")(ctx, title, lines)


async def _send_resource_notice(ctx, title_key, body_key, **kwargs):
    await _import("_send_resource_notice")(ctx, title_key, body_key, **kwargs)


def _default_mode():
    return _import("_default_mode")()


def _enabled():
    return _import("_enabled")()


def _get_client():
    return _import("_get_client")()


def _score_args(args: str) -> tuple[str, str]:
    text = args.strip()
    if not text:
        return "best", ""
    parts = text.split(maxsplit=1)
    head = parts[0].casefold()
    aliases = {
        "best": "best", "bp": "best", "最好": "best",
        "recent": "recent", "rs": "recent", "最近": "recent",
        "firsts": "firsts", "第一": "firsts",
    }
    if head in aliases:
        return aliases[head], parts[1] if len(parts) > 1 else ""
    return "best", text


def _extract_beatmap_id(text: str) -> int | None:
    raw = text.strip()
    if raw.isdigit():
        return int(raw)
    patterns = [r"osu\.ppy\.sh/(?:beatmaps|b)/(\d+)", r"osu\.ppy\.sh/beatmapsets/\d+#\w+/(\d+)"]
    for pattern in patterns:
        match = re.search(pattern, raw)
        if match:
            return int(match.group(1))
    return None


def _bound_target(ctx, mode, target, *, raw_args):
    return _import("_bound_target")(ctx, mode, target, raw_args=raw_args)


async def _get_bound_or_named_user(ctx, args):
    return await _import("_get_bound_or_named_user")(ctx, args)


async def _get_recent_scores_for_card(user_id, mode):
    return await _import("_get_recent_scores_for_card")(user_id, mode)


async def _upload_file(ctx, path, name):
    await _import("_upload_file")(ctx, path, name)


async def _send_download_link(ctx, beatmapset_id, reason):
    await _import("_send_download_link")(ctx, beatmapset_id, reason)


async def _resolve_download_beatmapset_id(text: str, mode: str) -> tuple[int, str]:
    from .downloader import extract_beatmapset_id
    beatmapset_id = extract_beatmapset_id(text)
    if beatmapset_id is not None:
        return beatmapset_id, "direct"
    beatmap_id = _extract_beatmap_id(text)
    if beatmap_id is not None:
        beatmap = await _get_client().get_beatmap(beatmap_id)
        set_id = beatmap.get("beatmapset_id") or (beatmap.get("beatmapset") or {}).get("id")
        if not set_id:
            from .api import OsuApiError
            raise OsuApiError("谱面详情里没有 beatmapset_id")
        return int(set_id), "beatmap"
    result = await _get_client().search_beatmapsets(text, mode=mode)
    from .api import OsuNotFoundError
    beatmapsets = list(result.get("beatmapsets") or [])
    if not beatmapsets:
        raise OsuNotFoundError("没有找到可下载的谱面")
    return int(beatmapsets[0]["id"]), "search"


async def handle_osu_help(ctx: CommandContext) -> None:
    await _send_notice(ctx, msg("osu.help_title"), msg("osu.help").splitlines())


async def handle_osu_bind(ctx: CommandContext) -> None:
    if not _enabled():
        return
    mode, target = split_mode_and_target(ctx.args, _default_mode())
    if not target:
        await _send_resource_notice(ctx, "missing_username_title", "bind_usage")
        return
    try:
        user = await _get_client().get_user(target, mode)
        set_binding(ctx.event.get_user_id(), osu_id=int(user["id"]), username=str(user.get("username") or target), mode=mode)
        path = await render_user_card(user, mode, _cache_dir(), title=msg("osu.bind_success_title"), proxy=_proxy())
        await _send_image(ctx, path)
    except OsuAuthError as e:
        await _send_resource_notice(ctx, "config_error_title", "config_error", error=e)
    except OsuNotFoundError:
        await _send_resource_notice(ctx, "user_not_found_title", "target", target=target)


async def handle_osu_unbind(ctx: CommandContext) -> None:
    existed = remove_binding(ctx.event.get_user_id())
    await _send_notice(ctx, msg("osu.unbind_title"), [msg("osu.unbind_success") if existed else msg("osu.unbind_empty")])


async def handle_osu_user(ctx: CommandContext) -> None:
    if not _enabled():
        return
    try:
        result = await _get_bound_or_named_user(ctx, ctx.args)
        if result is None:
            return
        user, mode = result
        recent_scores = await _get_recent_scores_for_card(int(user["id"]), mode)
        await _send_image(ctx, await render_user_card(user, mode, _cache_dir(), proxy=_proxy(), recent_scores=recent_scores))
        stats_increment(ctx.event, "osu_queries", 1)
    except OsuAuthError as e:
        await _send_resource_notice(ctx, "config_error_title", "config_error", error=e)
    except OsuNotFoundError:
        await _send_notice(ctx, msg("osu.user_not_found_title"), [ctx.args or msg("osu.bound_account")])
    except OsuApiError as e:
        await _send_notice(ctx, msg("osu.query_failed_title"), [str(e)])


async def handle_osu_dashboard(ctx: CommandContext) -> None:
    if not _enabled():
        return
    try:
        result = await _get_bound_or_named_user(ctx, ctx.args)
        if result is None:
            return
        user, mode = result
        scores = await _get_client().get_user_scores(int(user["id"]), mode, "recent", limit=int(get_config().get("score_limit") or 5))
        await _send_image(ctx, await render_dashboard(user, scores, mode, _cache_dir(), proxy=_proxy()))
        stats_increment(ctx.event, "osu_queries", 1)
    except OsuAuthError as e:
        await _send_resource_notice(ctx, "config_error_title", "config_error", error=e)
    except OsuNotFoundError:
        await _send_notice(ctx, msg("osu.user_not_found_title"), [ctx.args or msg("osu.bound_account")])
    except OsuApiError as e:
        await _send_notice(ctx, msg("osu.query_failed_title"), [str(e)])


async def handle_osu_scores(ctx: CommandContext) -> None:
    if not _enabled():
        return
    score_type, rest = _score_args(ctx.args)
    try:
        result = await _get_bound_or_named_user(ctx, rest)
        if result is None:
            return
        user, mode = result
        scores = await _get_client().get_user_scores(int(user["id"]), mode, score_type, limit=int(get_config().get("score_limit") or 5))
        await _send_image(ctx, await render_scores(user, scores, mode, score_type, _cache_dir()))
        stats_increment(ctx.event, "osu_queries", 1)
    except OsuAuthError as e:
        await _send_resource_notice(ctx, "config_error_title", "config_error", error=e)
    except OsuNotFoundError:
        await _send_notice(ctx, msg("osu.user_not_found_title"), [rest or msg("osu.bound_account")])
    except OsuApiError as e:
        await _send_notice(ctx, msg("osu.query_failed_title"), [str(e)])


async def handle_osu_ranking(ctx: CommandContext) -> None:
    if not _enabled():
        return
    parts = ctx.args.split()
    mode = _default_mode()
    country = None
    variant = None
    for part in parts:
        folded = part.casefold()
        if folded in MODE_ALIASES:
            mode = normalize_mode(folded, mode)
        elif mode == "mania" and folded in {"4k", "7k"}:
            variant = folded
        elif re.fullmatch(r"[a-zA-Z]{2}", part):
            country = part.upper()
    try:
        data = await _get_client().get_ranking(mode, country=country, variant=variant)
        await _send_image(ctx, await render_ranking(data, mode, _cache_dir(), country=country, limit=int(get_config().get("ranking_limit") or 10)))
        stats_increment(ctx.event, "osu_queries", 1)
    except OsuAuthError as e:
        await _send_resource_notice(ctx, "config_error_title", "config_error", error=e)
    except OsuApiError as e:
        await _send_notice(ctx, msg("osu.query_failed_title"), [str(e)])


async def handle_osu_beatmap(ctx: CommandContext) -> None:
    if not _enabled():
        return
    text = ctx.args.strip()
    if not text:
        await _send_resource_notice(ctx, "missing_beatmap_title", "beatmap_usage")
        return
    mode, query = split_mode_and_target(text, _default_mode())
    beatmap_id = _extract_beatmap_id(query or text)
    try:
        if beatmap_id is not None:
            beatmap = await _get_client().get_beatmap(beatmap_id)
            await _send_image(ctx, await render_beatmap(beatmap, _cache_dir(), proxy=_proxy()))
        else:
            result = await _get_client().search_beatmapsets(query or text, mode=mode)
            await _send_image(ctx, await render_beatmap_search(result, query or text, mode, _cache_dir(), limit=int(get_config().get("beatmap_search_limit") or 5)))
        stats_increment(ctx.event, "osu_queries", 1)
    except OsuAuthError as e:
        await _send_resource_notice(ctx, "config_error_title", "config_error", error=e)
    except OsuNotFoundError:
        await _send_notice(ctx, msg("osu.beatmap_not_found_title"), [text])
    except OsuApiError as e:
        await _send_notice(ctx, msg("osu.query_failed_title"), [str(e)])


async def handle_osu_download(ctx: CommandContext) -> None:
    if not _enabled():
        return
    text = ctx.args.strip()
    if not text:
        await _send_resource_notice(ctx, "missing_download_title", "download_usage")
        return
    mode, query = split_mode_and_target(text, _default_mode())
    beatmapset_id = None
    try:
        beatmapset_id, _ = await _resolve_download_beatmapset_id(query or text, mode)
        cfg = get_config()
        downloaded = await download_beatmapset_from_official(
            beatmapset_id, cache_dir=_cache_dir(),
            no_video=bool(cfg.get("download_no_video", True)),
            max_file_mb=int(cfg.get("download_max_file_mb") or 80),
            session_cookie=str(cfg.get("session_cookie") or ""),
            proxy=_proxy(), timeout=float(cfg.get("timeout") or 60),
        )
        await _upload_file(ctx, downloaded.path, f"osu_{beatmapset_id}.osz")
        stats_increment(ctx.event, "osu_queries", 1)
    except OsuDownloadNeedsLogin as e:
        await _send_download_link(ctx, beatmapset_id, str(e))
    except OsuDownloadError as e:
        await _send_download_link(ctx, beatmapset_id, str(e))
    except OsuAuthError as e:
        await _send_resource_notice(ctx, "config_error_title", "config_error", error=e)
    except OsuNotFoundError:
        await _send_notice(ctx, msg("osu.beatmap_not_found_title"), [text])
    except OsuApiError as e:
        await _send_notice(ctx, msg("osu.query_failed_title"), [str(e)])
    except Exception as e:
        if beatmapset_id is None:
            await _send_resource_notice(ctx, "download_failed_title", "upload_failed", error_type=type(e).__name__)
        else:
            await _send_download_link(ctx, beatmapset_id, msg("osu.upload_failed", error_type=type(e).__name__))
