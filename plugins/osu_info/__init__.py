from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment, PrivateMessageEvent

from core.ai_tool_registry import AIToolContext, register_ai_tool
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
    mode_label,
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


@register_ai_tool(
    "osu_user_lookup",
    plugin_name="osu_info",
    description="Look up a public osu! user profile. If target is omitted, the current QQ user's osu! binding is used when available.",
    parameters={
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "osu! username or user ID. Optional when the QQ user has a binding.",
            },
            "mode": {
                "type": "string",
                "description": "osu! ruleset.",
                "enum": ["osu", "taiko", "fruits", "mania"],
            },
        },
        "additionalProperties": False,
    },
)
async def ai_tool_osu_user_lookup(context: AIToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    if not _enabled():
        return {"error": "osu_info is disabled"}
    resolved = _ai_resolve_osu_target(context, arguments)
    if resolved is None:
        return {"error": "target is required when current QQ user has no osu! binding"}
    mode, target = resolved
    try:
        user = await _get_client().get_user(target, mode)
    except OsuNotFoundError:
        return {"mode": mode, "target": target, "not_found": True}
    except OsuApiError as e:
        logger.warning("[osu] AI Tool 用户查询失败 target=%r mode=%s: %s", target, mode, e)
        return {"mode": mode, "target": target, "error": str(e)}
    return {"mode": mode, "user": _ai_user_payload(user)}


@register_ai_tool(
    "osu_scores_lookup",
    plugin_name="osu_info",
    description="Look up a public osu! user's best, recent, or first-place scores.",
    parameters={
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "osu! username or user ID. Optional when the QQ user has a binding.",
            },
            "mode": {
                "type": "string",
                "description": "osu! ruleset.",
                "enum": ["osu", "taiko", "fruits", "mania"],
            },
            "score_type": {
                "type": "string",
                "description": "Score list type.",
                "enum": ["best", "recent", "firsts"],
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of scores to return.",
                "minimum": 1,
                "maximum": 10,
            },
        },
        "additionalProperties": False,
    },
)
async def ai_tool_osu_scores_lookup(context: AIToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    if not _enabled():
        return {"error": "osu_info is disabled"}
    resolved = _ai_resolve_osu_target(context, arguments)
    if resolved is None:
        return {"error": "target is required when current QQ user has no osu! binding"}
    mode, target = resolved
    score_type = str(arguments.get("score_type") or "best").strip().casefold()
    if score_type not in {"best", "recent", "firsts"}:
        score_type = "best"
    limit = _ai_int(arguments.get("limit"), default=int(get_config().get("score_limit") or 5), minimum=1, maximum=10)
    try:
        user = await _get_client().get_user(target, mode)
        scores = await _get_client().get_user_scores(int(user["id"]), mode, score_type, limit=limit)
    except OsuNotFoundError:
        return {"mode": mode, "target": target, "not_found": True}
    except OsuApiError as e:
        logger.warning("[osu] AI Tool 成绩查询失败 target=%r mode=%s type=%s: %s", target, mode, score_type, e)
        return {"mode": mode, "target": target, "score_type": score_type, "error": str(e)}
    return {
        "mode": mode,
        "score_type": score_type,
        "user": _ai_user_payload(user),
        "scores": [_ai_score_payload(score) for score in scores[:limit]],
    }


@register_ai_tool(
    "osu_beatmap_lookup",
    plugin_name="osu_info",
    description="Look up an osu! beatmap by beatmap ID/link or search beatmapsets by keyword.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Beatmap ID, beatmap URL, or search keyword.",
            },
            "mode": {
                "type": "string",
                "description": "osu! ruleset used for keyword search.",
                "enum": ["osu", "taiko", "fruits", "mania"],
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of keyword search results to return.",
                "minimum": 1,
                "maximum": 10,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)
async def ai_tool_osu_beatmap_lookup(context: AIToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    if not _enabled():
        return {"error": "osu_info is disabled"}
    query = str(arguments.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    mode = normalize_mode(str(arguments.get("mode") or ""), _default_mode())
    limit = _ai_int(arguments.get("limit"), default=int(get_config().get("beatmap_search_limit") or 5), minimum=1, maximum=10)
    try:
        beatmap_id = _extract_beatmap_id(query)
        if beatmap_id is not None:
            beatmap = await _get_client().get_beatmap(beatmap_id)
            return {"query": query, "mode": mode, "beatmap": _ai_beatmap_payload(beatmap)}
        result = await _get_client().search_beatmapsets(query, mode=mode)
    except OsuNotFoundError:
        return {"query": query, "mode": mode, "not_found": True}
    except OsuApiError as e:
        logger.warning("[osu] AI Tool 谱面查询失败 query=%r mode=%s: %s", query, mode, e)
        return {"query": query, "mode": mode, "error": str(e)}
    beatmapsets = result.get("beatmapsets") if isinstance(result, dict) else []
    if not isinstance(beatmapsets, list):
        beatmapsets = []
    return {
        "query": query,
        "mode": mode,
        "results": [_ai_beatmapset_payload(item) for item in beatmapsets[:limit] if isinstance(item, dict)],
    }


@register_ai_tool(
    "osu_ranking_lookup",
    plugin_name="osu_info",
    description="Look up public osu! global ranking entries by mode, optional country code, and optional mania variant.",
    parameters={
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "description": "osu! ruleset.",
                "enum": ["osu", "taiko", "fruits", "mania"],
            },
            "country": {
                "type": "string",
                "description": "Optional ISO country code such as JP or US.",
            },
            "variant": {
                "type": "string",
                "description": "Optional mania variant.",
                "enum": ["4k", "7k"],
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of ranking entries to return.",
                "minimum": 1,
                "maximum": 20,
            },
        },
        "additionalProperties": False,
    },
)
async def ai_tool_osu_ranking_lookup(context: AIToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    if not _enabled():
        return {"error": "osu_info is disabled"}
    mode = normalize_mode(str(arguments.get("mode") or ""), _default_mode())
    country = str(arguments.get("country") or "").strip().upper()[:2] or None
    variant = str(arguments.get("variant") or "").strip().casefold()
    if mode != "mania" or variant not in {"4k", "7k"}:
        variant = None
    limit = _ai_int(arguments.get("limit"), default=int(get_config().get("ranking_limit") or 10), minimum=1, maximum=20)
    try:
        data = await _get_client().get_ranking(mode, country=country, variant=variant)
    except OsuApiError as e:
        logger.warning("[osu] AI Tool 排名查询失败 mode=%s country=%s variant=%s: %s", mode, country, variant, e)
        return {"mode": mode, "country": country or "", "variant": variant or "", "error": str(e)}
    ranking = data.get("ranking") if isinstance(data, dict) else []
    if not isinstance(ranking, list):
        ranking = []
    return {
        "mode": mode,
        "country": country or "",
        "variant": variant or "",
        "ranking": [_ai_ranking_payload(item) for item in ranking[:limit] if isinstance(item, dict)],
    }


def _ai_resolve_osu_target(context: AIToolContext, arguments: dict[str, Any]) -> tuple[str, str] | None:
    mode = normalize_mode(str(arguments.get("mode") or ""), _default_mode())
    target = str(arguments.get("target") or "").strip()
    if target:
        return mode, target
    event = context.event if context is not None else None
    if event is None:
        return None
    binding = get_binding(event.get_user_id())
    if binding is None:
        return None
    return binding.mode or mode, str(binding.osu_id)


def _ai_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return min(max(parsed, minimum), maximum)


def _ai_user_payload(user: dict[str, Any]) -> dict[str, Any]:
    stats = user.get("statistics") if isinstance(user.get("statistics"), dict) else {}
    country = user.get("country") if isinstance(user.get("country"), dict) else {}
    return {
        "id": user.get("id"),
        "username": user.get("username"),
        "mode": mode_label(str(user.get("playmode") or "")),
        "country_code": user.get("country_code") or country.get("code") or "",
        "global_rank": stats.get("global_rank"),
        "country_rank": stats.get("country_rank"),
        "pp": stats.get("pp"),
        "hit_accuracy": stats.get("hit_accuracy"),
        "play_count": stats.get("play_count"),
        "ranked_score": stats.get("ranked_score"),
        "level": (stats.get("level") or {}).get("current") if isinstance(stats.get("level"), dict) else None,
        "profile_url": f"https://osu.ppy.sh/users/{user.get('id')}" if user.get("id") else "",
    }


def _ai_score_payload(score: dict[str, Any]) -> dict[str, Any]:
    beatmap = score.get("beatmap") if isinstance(score.get("beatmap"), dict) else {}
    beatmapset = score.get("beatmapset") if isinstance(score.get("beatmapset"), dict) else {}
    return {
        "rank": score.get("rank"),
        "pp": score.get("pp"),
        "accuracy": score.get("accuracy"),
        "score": score.get("score"),
        "max_combo": score.get("max_combo"),
        "mods": score.get("mods") if isinstance(score.get("mods"), list) else [],
        "ended_at": score.get("ended_at") or score.get("created_at") or "",
        "beatmap": {
            "id": beatmap.get("id"),
            "version": beatmap.get("version"),
            "difficulty_rating": beatmap.get("difficulty_rating"),
            "url": beatmap.get("url") or (f"https://osu.ppy.sh/beatmaps/{beatmap.get('id')}" if beatmap.get("id") else ""),
        },
        "beatmapset": {
            "id": beatmapset.get("id") or beatmap.get("beatmapset_id"),
            "title": beatmapset.get("title"),
            "artist": beatmapset.get("artist"),
            "creator": beatmapset.get("creator"),
        },
    }


def _ai_beatmap_payload(beatmap: dict[str, Any]) -> dict[str, Any]:
    beatmapset = beatmap.get("beatmapset") if isinstance(beatmap.get("beatmapset"), dict) else {}
    return {
        "id": beatmap.get("id"),
        "beatmapset_id": beatmap.get("beatmapset_id") or beatmapset.get("id"),
        "url": beatmap.get("url") or (f"https://osu.ppy.sh/beatmaps/{beatmap.get('id')}" if beatmap.get("id") else ""),
        "mode": beatmap.get("mode"),
        "version": beatmap.get("version"),
        "difficulty_rating": beatmap.get("difficulty_rating"),
        "total_length": beatmap.get("total_length"),
        "bpm": beatmap.get("bpm"),
        "cs": beatmap.get("cs"),
        "ar": beatmap.get("ar"),
        "accuracy": beatmap.get("accuracy"),
        "drain": beatmap.get("drain"),
        "playcount": beatmap.get("playcount"),
        "passcount": beatmap.get("passcount"),
        "beatmapset": _ai_beatmapset_payload(beatmapset),
    }


def _ai_beatmapset_payload(beatmapset: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": beatmapset.get("id"),
        "title": beatmapset.get("title"),
        "artist": beatmapset.get("artist"),
        "creator": beatmapset.get("creator"),
        "status": beatmapset.get("status"),
        "play_count": beatmapset.get("play_count"),
        "favourite_count": beatmapset.get("favourite_count"),
        "url": f"https://osu.ppy.sh/beatmapsets/{beatmapset.get('id')}" if beatmapset.get("id") else "",
    }


def _ai_ranking_payload(item: dict[str, Any]) -> dict[str, Any]:
    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    return {
        "rank": item.get("global_rank") or item.get("rank"),
        "pp": item.get("pp"),
        "hit_accuracy": item.get("hit_accuracy"),
        "play_count": item.get("play_count"),
        "ranked_score": item.get("ranked_score"),
        "user": {
            "id": user.get("id"),
            "username": user.get("username"),
            "country_code": user.get("country_code"),
            "profile_url": f"https://osu.ppy.sh/users/{user.get('id')}" if user.get("id") else "",
        },
    }


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
