from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment, PrivateMessageEvent

from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command
from core.stats_tracker import increment as stats_increment

from .api import (
    MODE_ALIASES,
    OsuApiClient,
    OsuApiError,
    OsuAuthError,
    OsuNotFoundError,
    normalize_mode,
    split_mode_and_target,
)
from .config import get_config
from .downloader import (
    OsuDownloadError,
    OsuDownloadNeedsLogin,
    download_beatmapset_from_official,
    extract_beatmapset_id,
    official_download_url,
    official_page_url,
)
from .render import (
    render_beatmap,
    render_beatmap_search,
    render_dashboard,
    render_notice,
    render_ranking,
    render_scores,
    render_user_card,
)
from .storage import get_binding, remove_binding, set_binding

logger = logging.getLogger("HikariBot.OsuInfo")

_client: OsuApiClient | None = None
_client_key: tuple[str, str, str, str] | None = None

_SUBCOMMAND_ALIASES = {
    "help": "help",
    "帮助": "help",
    "菜单": "help",
    "bind": "bind",
    "绑定": "bind",
    "unbind": "unbind",
    "解绑": "unbind",
    "user": "user",
    "用户": "user",
    "信息": "user",
    "profile": "user",
    "看板": "dashboard",
    "卡片": "dashboard",
    "card": "dashboard",
    "dashboard": "dashboard",
    "score": "scores",
    "scores": "scores",
    "成绩": "scores",
    "bp": "scores",
    "ranking": "ranking",
    "rank": "ranking",
    "排名": "ranking",
    "排行榜": "ranking",
    "beatmap": "beatmap",
    "map": "beatmap",
    "谱面": "beatmap",
    "download": "download",
    "dl": "download",
    "下载": "download",
}


def _cache_dir() -> Path:
    return Path(str(get_config().get("cache_dir") or "/tmp/hikari_bot/osu_info"))


def _proxy() -> str:
    return str(get_config().get("proxy") or "").strip()


def _get_client() -> OsuApiClient:
    global _client, _client_key
    cfg = get_config()
    key = (
        str(cfg.get("client_id") or ""),
        str(cfg.get("client_secret") or ""),
        str(cfg.get("api_base") or ""),
        str(cfg.get("proxy") or ""),
    )
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


def _bound_target(
    ctx: CommandContext,
    mode: str,
    target: str,
    *,
    raw_args: str,
) -> tuple[str, str] | None:
    if target:
        return mode, target
    binding = get_binding(ctx.event.get_user_id())
    if binding is None:
        return None
    if not raw_args.strip():
        mode = binding.mode
    return mode, str(binding.osu_id)


async def _get_bound_or_named_user(ctx: CommandContext, args: str) -> tuple[dict[str, Any], str] | None:
    mode, target = split_mode_and_target(args, _default_mode())
    resolved = _bound_target(ctx, mode, target, raw_args=args)
    if resolved is None:
        await _send_resource_notice(ctx, "needs_binding_title", "needs_binding")
        return None
    mode, target = resolved
    user = await _get_client().get_user(target, mode)
    return user, mode


async def _get_recent_scores_for_card(user_id: int, mode: str) -> list[dict[str, Any]]:
    try:
        return await _get_client().get_user_scores(
            user_id,
            mode,
            "recent",
            limit=min(int(get_config().get("score_limit") or 5), 3),
        )
    except OsuApiError as e:
        logger.info("[osu] 最近成绩获取失败，继续渲染用户资料: %s", e)
        return []


def _score_args(args: str) -> tuple[str, str]:
    text = args.strip()
    if not text:
        return "best", ""
    parts = text.split(maxsplit=1)
    head = parts[0].casefold()
    aliases = {
        "best": "best",
        "bp": "best",
        "最好": "best",
        "recent": "recent",
        "rs": "recent",
        "最近": "recent",
        "firsts": "firsts",
        "第一": "firsts",
    }
    if head in aliases:
        return aliases[head], parts[1] if len(parts) > 1 else ""
    return "best", text


def _extract_beatmap_id(text: str) -> int | None:
    raw = text.strip()
    if raw.isdigit():
        return int(raw)
    patterns = [
        r"osu\.ppy\.sh/(?:beatmaps|b)/(\d+)",
        r"osu\.ppy\.sh/beatmapsets/\d+#\w+/(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw)
        if match:
            return int(match.group(1))
    return None


async def _resolve_download_beatmapset_id(text: str, mode: str) -> tuple[int, str]:
    beatmapset_id = extract_beatmapset_id(text)
    if beatmapset_id is not None:
        return beatmapset_id, "direct"

    beatmap_id = _extract_beatmap_id(text)
    if beatmap_id is not None:
        beatmap = await _get_client().get_beatmap(beatmap_id)
        set_id = beatmap.get("beatmapset_id") or (beatmap.get("beatmapset") or {}).get("id")
        if not set_id:
            raise OsuApiError("谱面详情里没有 beatmapset_id")
        return int(set_id), "beatmap"

    result = await _get_client().search_beatmapsets(text, mode=mode)
    beatmapsets = list(result.get("beatmapsets") or [])
    if not beatmapsets:
        raise OsuNotFoundError("没有找到可下载的谱面")
    return int(beatmapsets[0]["id"]), "search"


async def _upload_file(ctx: CommandContext, path: Path, name: str) -> None:
    bot = ctx.bot
    event = ctx.event
    if isinstance(event, GroupMessageEvent):
        await bot.call_api(
            "upload_group_file",
            group_id=event.group_id,
            file=str(path.resolve()),
            name=name,
        )
        return
    if isinstance(event, PrivateMessageEvent):
        await bot.call_api(
            "upload_private_file",
            user_id=event.user_id,
            file=str(path.resolve()),
            name=name,
        )
        return
    raise RuntimeError(f"不支持的事件类型，无法上传文件: {type(event).__name__}")


async def _send_download_link(ctx: CommandContext, beatmapset_id: int, reason: str) -> None:
    await ctx.send(
        Message(
            msg(
                "osu.download_link",
                download_url=official_download_url(beatmapset_id, no_video=bool(get_config().get("download_no_video", True))),
                page_url=official_page_url(beatmapset_id),
                reason=reason,
            )
        )
    )


async def handle_osu_help(ctx: CommandContext) -> None:
    await _send_notice(
        ctx,
        msg("osu.help_title"),
        msg("osu.help").splitlines(),
    )


async def handle_osu_bind(ctx: CommandContext) -> None:
    if not _enabled():
        return
    mode, target = split_mode_and_target(ctx.args, _default_mode())
    if not target:
        await _send_resource_notice(ctx, "missing_username_title", "bind_usage")
        return
    try:
        user = await _get_client().get_user(target, mode)
        binding = set_binding(
            ctx.event.get_user_id(),
            osu_id=int(user["id"]),
            username=str(user.get("username") or target),
            mode=mode,
        )
        path = await render_user_card(
            user,
            mode,
            _cache_dir(),
            title=msg("osu.bind_success_title"),
            proxy=_proxy(),
        )
        await _send_image(ctx, path)
        logger.info("[osu] QQ %s 绑定 osu! %s(%s)", binding.qq, binding.username, binding.osu_id)
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
        await _send_image(
            ctx,
            await render_user_card(
                user,
                mode,
                _cache_dir(),
                proxy=_proxy(),
                recent_scores=recent_scores,
            ),
        )
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
        scores = await _get_client().get_user_scores(
            int(user["id"]),
            mode,
            "recent",
            limit=int(get_config().get("score_limit") or 5),
        )
        await _send_image(
            ctx,
            await render_dashboard(user, scores, mode, _cache_dir(), proxy=_proxy()),
        )
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
        scores = await _get_client().get_user_scores(
            int(user["id"]),
            mode,
            score_type,
            limit=int(get_config().get("score_limit") or 5),
        )
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
    country: str | None = None
    variant: str | None = None
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
        await _send_image(
            ctx,
            await render_ranking(
                data,
                mode,
                _cache_dir(),
                country=country,
                limit=int(get_config().get("ranking_limit") or 10),
            ),
        )
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
            await _send_image(
                ctx,
                await render_beatmap_search(
                    result,
                    query or text,
                    mode,
                    _cache_dir(),
                    limit=int(get_config().get("beatmap_search_limit") or 5),
                ),
            )
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
    beatmapset_id: int | None = None
    try:
        beatmapset_id, _source = await _resolve_download_beatmapset_id(query or text, mode)
        cfg = get_config()
        downloaded = await download_beatmapset_from_official(
            beatmapset_id,
            cache_dir=_cache_dir(),
            no_video=bool(cfg.get("download_no_video", True)),
            max_file_mb=int(cfg.get("download_max_file_mb") or 80),
            session_cookie=str(cfg.get("session_cookie") or ""),
            proxy=_proxy(),
            timeout=float(cfg.get("timeout") or 60),
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
    if subcommand == "help":
        await _call_with_args(ctx, rest, handle_osu_help)
    elif subcommand == "bind":
        await _call_with_args(ctx, rest, handle_osu_bind)
    elif subcommand == "unbind":
        await _call_with_args(ctx, rest, handle_osu_unbind)
    elif subcommand == "user":
        await _call_with_args(ctx, rest, handle_osu_user)
    elif subcommand == "dashboard":
        await _call_with_args(ctx, rest, handle_osu_dashboard)
    elif subcommand == "scores":
        await _call_with_args(ctx, rest, handle_osu_scores)
    elif subcommand == "ranking":
        await _call_with_args(ctx, rest, handle_osu_ranking)
    elif subcommand == "beatmap":
        await _call_with_args(ctx, rest, handle_osu_beatmap)
    elif subcommand == "download":
        await _call_with_args(ctx, rest, handle_osu_download)
    else:
        await _call_with_args(ctx, ctx.args, handle_osu_user)


get_config()
