"""Bandcamp 搜索插件 — 搜索厂牌、艺术家、专辑、单曲"""

from __future__ import annotations

import logging

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from core.ai_tool_registry import AIToolContext, register_ai_tool
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


def _format_results(results: BandcampSearchResults) -> str:
    """Format search results as a compact text list."""
    lines = [msg("bandcamp.search_header", query=results.query)]
    for i, r in enumerate(results.results, 1):
        icon = _TYPE_ICON.get(r.type, "📄")
        date_part = f" ({r.release_date})" if r.release_date else ""
        lines.append(
            msg(
                "bandcamp.result_item",
                index=i,
                icon=icon,
                type=r.type,
                title=r.title,
                artist=r.artist,
                date=date_part,
            )
        )
    # Append URLs at the end so the list stays readable
    for i, r in enumerate(results.results, 1):
        lines.append(f"  {i}. {r.url}")
    return "\n".join(lines)


# ---- AI Tool -----------------------------------------------------------------


@register_ai_tool(
    "bandcamp_search",
    plugin_name="bandcamp_wiki",
    description=(
        "Search Bandcamp for music, albums, artists, and labels."
        " Returns a list of results with title, artist/label, type, URL, release date,"
        " and thumbnail.  Useful for finding obscure/independent music and labels."
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

    try:
        results = await BandcampClient(get_config()).search(keyword, type_filter=type_filter)
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
                "release_date": r.release_date,
                "thumbnail": r.thumbnail,
            }
            for r in results.results
        ],
    }


# ---- Command -----------------------------------------------------------------


@command(
    "bandcamp",
    aliases=(
        "bc",
        "bandcamp搜索",
        "Bandcamp",
    ),
    description="搜索 Bandcamp 上的音乐、厂牌和艺术家",
    usage="bandcamp <关键词>\n"
    "  bandcamp label <关键词>  搜索厂牌/艺术家\n"
    "  bandcamp album <关键词>  搜索专辑\n"
    "  bandcamp track <关键词>  搜索单曲",
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
        kw = parts[0].lower()
        _SUB_MAP = {
            "album": "album",
            "track": "track",
            "label": "artist/label",
            "厂牌": "artist/label",
            "artist": "artist/label",
            "艺术家": "artist/label",
            "音乐人": "artist/label",
        }
        if kw in _SUB_MAP:
            type_filter = _SUB_MAP[kw]
            query = parts[1]

    stats_increment(ctx.event, "bandcamp_queries", 1)

    try:
        results = await BandcampClient(get_config()).search(query, type_filter=type_filter)
    except BandcampNotFound:
        await ctx.send(Message(msg("bandcamp.not_found", query=query)))
        return
    except BandcampError as exc:
        logger.warning("[Bandcamp] 查询失败 query=%r error=%s", query, exc)
        await ctx.send(Message(msg("bandcamp.failed", error=exc)))
        return

    await _send_results(ctx, results)


async def _send_results(ctx: CommandContext, results: BandcampSearchResults) -> None:
    text = _format_results(results)
    await ctx.send(Message(text))

    # Send thumbnail of the first hit — a picture helps identify the result
    first = results.results[0]
    if first.thumbnail:
        try:
            caption = msg("bandcamp.thumbnail", title=first.title)
            await ctx.send(Message(caption + "\n") + MessageSegment.image(first.thumbnail))
        except Exception as exc:
            logger.debug("[Bandcamp] 缩略图发送失败: %s", exc)


get_config()
