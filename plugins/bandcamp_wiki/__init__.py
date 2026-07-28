"""Bandcamp 搜索插件 — 搜索厂牌、艺术家、专辑、单曲"""

from __future__ import annotations

import logging

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment

from core.ai_tool_registry import AIToolContext, register_ai_tool
from core.bot_identity import get_bot_name
from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command
from core.stats_tracker import increment as stats_increment

from .api import BandcampClient, BandcampError, BandcampNotFound, BandcampResult, BandcampSearchResults
from .config import get_config

logger = logging.getLogger("HikariBot.BandcampWiki")

# ---- helpers -----------------------------------------------------------------


def _enabled() -> bool:
    return bool(get_config().get("enabled", True))


_TYPE_ICON = {
    "album": "💿",
    "track": "🎵",
    "artist/label": "🏷️",
}


def _is_url_like(query: str) -> bool:
    """Check if *query* looks like a partial Bandcamp URL.

    ``taishi/compllege`` → ``True``
    ``taishi``          → ``False`` (plain artist name)
    ``label/11015``     → ``True``
    """
    q = query.strip()
    # Full URL
    if q.startswith(("http://", "https://", "bandcamp.com/")):
        return True
    # Contains a slash = likely subdomain/path or ID lookup
    if "/" in q and " " not in q:
        return True
    return False


def _normalise_partial_url(query: str) -> str:
    """Turn a partial URL into a full ``https://`` URL."""
    q = query.strip()
    if q.startswith("http"):
        return q
    if q.startswith("bandcamp.com/"):
        return f"https://{q}"
    # subdomain/path → https://subdomain.bandcamp.com/path
    if "/" in q:
        sub, _, path = q.partition("/")
        return f"https://{sub}.bandcamp.com/{path}"
    return f"https://{q}.bandcamp.com"


# ---- AI Tool -----------------------------------------------------------------


@register_ai_tool(
    "bandcamp_search",
    plugin_name="bandcamp_wiki",
    description=(
        "Search Bandcamp for music, albums, artists, and labels."
        " Returns a list of results with title, artist/label, type, URL,"
        " and a short snippet.  Useful for finding obscure/independent"
        " music and labels."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Bandcamp search keyword — artist, label, album, track, or genre name.",
            },
            "type_filter": {
                "type": "string",
                "description": "Optional filter: 'album', 'track', or 'artist/label'.",
                "enum": ["", "album", "track", "artist/label"],
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)
async def ai_tool_bandcamp_search(
    context: AIToolContext, arguments: dict[str, object]
) -> dict[str, object]:
    if not _enabled():
        return {"error": "bandcamp_wiki is disabled"}

    keyword = str(arguments.get("query") or "").strip()
    if not keyword:
        return {"error": "query is required"}

    type_filter = str(arguments.get("type_filter") or "").strip() or None

    client = BandcampClient(get_config())
    try:
        results = await client.search(keyword, type_filter=type_filter)
    except BandcampNotFound:
        return {"query": keyword, "not_found": True, "results": []}
    except BandcampError as exc:
        logger.warning("[Bandcamp] AI Tool 查询失败 keyword=%r error=%s", keyword, exc)
        return {"query": keyword, "error": str(exc)}

    return {
        "query": keyword,
        "results": [
            {
                "title": r.title,
                "artist": r.artist,
                "type": r.type,
                "url": r.url,
                "description": r.description[:300] if r.description else "",
            }
            for r in results.results
        ],
    }


# ---- Command -----------------------------------------------------------------


@command(
    "bandcamp",
    aliases=("bc", "bandcamp搜索", "Bandcamp"),
    description="搜索 Bandcamp 上的音乐、厂牌和艺术家",
    usage="bandcamp <关键词>\n"
    "  bandcamp label <关键词>   搜索厂牌/艺术家\n"
    "  bandcamp album <关键词>   搜索专辑\n"
    "  bandcamp track <关键词>   搜索单曲\n"
    "  bandcamp taishi/专辑名    直接查看指定页面",
)
async def handle_bandcamp(ctx: CommandContext) -> None:
    if not _enabled():
        return

    raw = ctx.args.strip()
    if not raw:
        await ctx.send(Message(msg("bandcamp.usage")))
        return

    # Parse optional type filter from the first word
    type_filter: str | None = None
    query = raw
    parts = raw.split(maxsplit=1)
    if len(parts) == 2:
        _SUB_MAP = {
            "album": "album",
            "track": "track",
            "label": "artist/label",
            "厂牌": "artist/label",
            "artist": "artist/label",
            "艺术家": "artist/label",
            "音乐人": "artist/label",
        }
        if parts[0].lower() in _SUB_MAP:
            type_filter = _SUB_MAP[parts[0].lower()]
            query = parts[1]

    client = BandcampClient(get_config())

    # URL-like query → direct page lookup
    if _is_url_like(query):
        stats_increment(ctx.event, "bandcamp_queries", 1)
        try:
            url = _normalise_partial_url(query)
            result = await client.lookup_page(url)
        except Exception as exc:
            logger.warning("[Bandcamp] 页面查询失败 query=%r error=%s", query, exc)
            await ctx.send(Message(msg("bandcamp.failed", error=exc)))
            return

        if result is None:
            await ctx.send(Message(msg("bandcamp.not_found", query=query)))
            return

        await _send_single(ctx, result)
        return

    # Keyword search via SearXNG
    stats_increment(ctx.event, "bandcamp_queries", 1)

    try:
        results = await client.search(query, type_filter=type_filter)
    except BandcampNotFound:
        await ctx.send(Message(msg("bandcamp.not_found", query=query)))
        return
    except BandcampError as exc:
        logger.warning("[Bandcamp] 查询失败 query=%r error=%s", query, exc)
        await ctx.send(Message(msg("bandcamp.failed", error=exc)))
        return

    await _send_results(ctx, results)


async def _send_results(ctx: CommandContext, results: BandcampSearchResults) -> None:
    nodes = _build_search_nodes(ctx.bot.self_id, results)
    try:
        await _send_forward(ctx, nodes)
    except Exception as exc:
        logger.warning("[Bandcamp] 合并转发失败 query=%r error=%s", results.query, exc)
        await _send_separate_search(ctx, results)


def _build_search_nodes(self_id: str, results: BandcampSearchResults) -> list[MessageSegment]:
    nodes: list[MessageSegment] = []
    # Header
    header = msg("bandcamp.search_header", query=results.query)
    nodes.append(_node(self_id, Message(header)))
    # Each result as a node
    for i, r in enumerate(results.results, 1):
        icon = _TYPE_ICON.get(r.type, "📄")
        artist_info = f" — {r.artist}" if r.artist else ""
        line = msg("bandcamp.result_item", index=i, icon=icon, type=r.type, title=r.title, artist=artist_info)
        if r.description:
            line += f"\n{r.description[:200]}"
        line += f"\n{r.url}"
        nodes.append(_node(self_id, Message(line)))
    return nodes


async def _send_forward(ctx: CommandContext, nodes: list[MessageSegment]) -> None:
    if isinstance(ctx.event, GroupMessageEvent):
        await ctx.bot.send_group_forward_msg(group_id=ctx.event.group_id, messages=nodes)
        return
    await ctx.bot.send_private_forward_msg(user_id=int(ctx.event.get_user_id()), messages=nodes)


def _node(self_id: str, content: Message) -> MessageSegment:
    return MessageSegment.node_custom(
        user_id=int(self_id),
        nickname=get_bot_name(),
        content=content,
    )


async def _send_separate_search(ctx: CommandContext, results: BandcampSearchResults) -> None:
    header = msg("bandcamp.search_header", query=results.query)
    await ctx.send(Message(header))
    for r in results.results:
        icon = _TYPE_ICON.get(r.type, "📄")
        artist_info = f" — {r.artist}" if r.artist else ""
        line = msg("bandcamp.result_item", index=0, icon=icon, type=r.type, title=r.title, artist=artist_info)
        if r.description:
            line += f"\n{r.description[:200]}"
        line += f"\n{r.url}"
        await ctx.send(Message(line))


async def _send_single(ctx: CommandContext, result: BandcampResult) -> None:
    nodes = _build_single_nodes(ctx.bot.self_id, result)
    try:
        await _send_forward(ctx, nodes)
    except Exception as exc:
        logger.warning("[Bandcamp] 合并转发失败 title=%r error=%s", result.title, exc)
        await _send_separate_single(ctx, result)


def _build_single_nodes(self_id: str, result: BandcampResult) -> list[MessageSegment]:
    nodes: list[MessageSegment] = []
    icon = _TYPE_ICON.get(result.type, "📄")
    lines = [f"{icon} {result.title}"]
    if result.artist:
        lines.append(f"  作者: {result.artist}")
    lines.append(f"  {result.url}")
    nodes.append(_node(self_id, Message("\n".join(lines))))

    if result.description:
        nodes.append(_node(self_id, Message(result.description[:500])))

    if result.thumbnail:
        try:
            caption = msg("bandcamp.thumbnail", title=result.title)
            nodes.append(
                _node(self_id, Message(caption + "\n") + MessageSegment.image(result.thumbnail))
            )
        except Exception as exc:
            logger.debug("[Bandcamp] 缩略图节点构建失败: %s", exc)

    return nodes


async def _send_separate_single(ctx: CommandContext, result: BandcampResult) -> None:
    icon = _TYPE_ICON.get(result.type, "📄")
    lines = [f"{icon} {result.title}"]
    if result.artist:
        lines.append(f"  作者: {result.artist}")
    lines.append(f"  {result.url}")
    await ctx.send(Message("\n".join(lines)))

    if result.description:
        await ctx.send(Message(result.description[:500]))

    if result.thumbnail:
        try:
            caption = msg("bandcamp.thumbnail", title=result.title)
            await ctx.send(Message(caption + "\n") + MessageSegment.image(result.thumbnail))
        except Exception as exc:
            logger.debug("[Bandcamp] 缩略图发送失败: %s", exc)


get_config()
