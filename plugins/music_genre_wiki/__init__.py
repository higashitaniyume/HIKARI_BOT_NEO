from __future__ import annotations

import logging
from typing import Any

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment

from core.ai_tool_registry import AIToolContext, register_ai_tool
from core.bot_identity import get_bot_name
from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command
from core.stats_tracker import increment as stats_increment

from .api import (
    GenreResult,
    MusicGenreClient,
    MusicGenreError,
    MusicGenreNotFound,
)
from .config import get_config

logger = logging.getLogger("HikariBot.MusicGenreWiki")


def _enabled() -> bool:
    return bool(get_config().get("enabled", True))


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


# ─── Genre Search ────────────────────────────────────────────────────────────


def _format_genre_intro(result: GenreResult) -> str:
    parts = [f"🎵 {result.name}"]
    if result.aka:
        parts.append(f"📎 别名：{result.aka}")
    parts.append(f"📂 分类：{result.chapter}")
    return "\n".join(parts)


def _format_genre_desc(result: GenreResult) -> str:
    return msg("music_genre.detail", title=result.name, detail=result.desc)


def _format_genre_examples(result: GenreResult) -> str:
    if not result.examples:
        return ""
    lines = [f"🎧 {result.name} — 代表曲目："]
    for i, ex in enumerate(result.examples, 1):
        lines.append(f"  {i}. {ex}")
    return "\n".join(lines)


@register_ai_tool(
    "music_genre_search",
    plugin_name="music_genre_wiki",
    description=(
        "Search the electronic music genre encyclopedia and return matching genre descriptions, "
        "example tracks, and related genre information."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Music genre name to search for (Chinese or English).",
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)
async def ai_tool_music_genre_search(
    context: AIToolContext, arguments: dict[str, object]
) -> dict[str, object]:
    if not _enabled():
        return {"error": "music_genre_wiki is disabled"}
    keyword = str(arguments.get("query") or "").strip()
    if not keyword:
        return {"error": "query is required"}
    try:
        results = MusicGenreClient(get_config()).search(keyword)
    except MusicGenreNotFound:
        return {"query": keyword, "not_found": True, "results": []}
    except MusicGenreError as e:
        logger.warning("[MusicGenreWiki] AI Tool 查询失败 keyword=%r error=%s", keyword, e)
        return {"query": keyword, "error": str(e)}
    return {
        "query": keyword,
        "results": [
            {
                "name": r.name,
                "description": r.desc,
                "chapter": r.chapter,
                "aka": r.aka,
                "parents": r.ups,
                "children": r.downs,
                "examples": r.examples,
            }
            for r in results
        ],
    }


@command(
    "流派",
    aliases=(
        "genre",
        "音乐流派",
        "电子音乐流派",
        "Genre",
        "流派查询",
    ),
    description="搜索电子音乐流派，显示详细介绍和代表曲目",
    usage="流派 <流派名称>",
)
async def handle_genre_search(ctx: CommandContext) -> None:
    if not _enabled():
        return

    keyword = ctx.args.strip()
    if not keyword:
        await ctx.send(Message(msg("music_genre.usage")))
        return

    stats_increment(ctx.event, "music_genre_queries", 1)
    client = MusicGenreClient(get_config())
    try:
        results = client.search(keyword)
        await _send_genre_results(ctx, results)
    except MusicGenreNotFound:
        await ctx.send(Message(msg("music_genre.not_found", keyword=keyword)))
    except MusicGenreError as e:
        logger.warning("[MusicGenreWiki] 查询失败 keyword=%r error=%s", keyword, e)
        await ctx.send(Message(msg("music_genre.failed", error=e)))


async def _send_genre_results(ctx: CommandContext, results: list[GenreResult]) -> None:
    """Send genre results. For a single result, send as forward; for multiple, list them."""
    if len(results) == 1:
        await _send_single_genre(ctx, results[0])
    else:
        lines = [msg("music_genre.multiple_results")]
        for r in results:
            lines.append(f"  · {r.name}（{r.chapter}）")
        await ctx.send(Message("\n".join(lines)))


async def _send_single_genre(ctx: CommandContext, result: GenreResult) -> None:
    nodes = [
        _node(ctx.bot.self_id, Message(_format_genre_intro(result))),
        _node(ctx.bot.self_id, Message(_format_genre_desc(result))),
    ]
    examples_text = _format_genre_examples(result)
    if examples_text:
        nodes.append(_node(ctx.bot.self_id, Message(examples_text)))
    try:
        await _send_forward(ctx, nodes)
    except Exception as e:
        logger.warning("[MusicGenreWiki] 合并转发失败 name=%r error=%s", result.name, e)
        await ctx.send(Message(_format_genre_intro(result)))
        await ctx.send(Message(_format_genre_desc(result)))
        if examples_text:
            await ctx.send(Message(examples_text))


# ─── Genre List (Chapters) ───────────────────────────────────────────────────


@command(
    "流派列表",
    aliases=(
        "genrelist",
        "GenreList",
        "流派分类",
        "音乐分类",
    ),
    description="列出所有电子音乐流派分类",
    usage="流派列表",
)
async def handle_genre_list(ctx: CommandContext) -> None:
    if not _enabled():
        return

    client = MusicGenreClient(get_config())
    chapters = client.list_chapters()
    lines = [msg("music_genre.chapter_list")]
    for ch in chapters:
        lines.append(f"  · {ch.name}（{ch.genre_count} 个流派）")
    await ctx.send(Message("\n".join(lines)))


# ─── Genre Tree ───────────────────────────────────────────────────────────────


@command(
    "流派树",
    aliases=(
        "genretree",
        "GenreTree",
        "流派结构",
    ),
    description="查看某个分类下的流派层级树",
    usage="流派树 <分类名称>",
)
async def handle_genre_tree(ctx: CommandContext) -> None:
    if not _enabled():
        return

    keyword = ctx.args.strip()
    if not keyword:
        await ctx.send(Message(msg("music_genre.tree_usage")))
        return

    client = MusicGenreClient(get_config())
    chapters = client.get_tree(keyword)
    if not chapters:
        await ctx.send(Message(msg("music_genre.chapter_not_found", chapter=keyword)))
        return

    ch = chapters[0]
    lines = [
        msg("music_genre.tree_header", chapter=ch.name, count=ch.genre_count),
        "",
    ]
    _flatten_tree(ch.tree, lines, prefix="")
    await ctx.send(Message("\n".join(lines)))


def _flatten_tree(tree: list[dict[str, Any]], lines: list[str], prefix: str) -> None:
    """Recursively flatten the genre tree into indented lines."""
    import re as _re

    for i, node in enumerate(tree):
        label = node.get("label", "?")
        children = node.get("children") or []
        is_last = i == len(tree) - 1
        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{label}")
        if children:
            extension = "    " if is_last else "│   "
            _flatten_tree(children, lines, prefix + extension)


# ─── Genre Relationships ─────────────────────────────────────────────────────


@command(
    "流派关系",
    aliases=(
        "genrerel",
        "GenreRel",
        "流派关联",
        "流派谱系",
    ),
    description="查看音乐流派的上游（影响来源）和下游（衍生分支）关系",
    usage="流派关系 <流派名称>",
)
async def handle_genre_relations(ctx: CommandContext) -> None:
    if not _enabled():
        return

    keyword = ctx.args.strip()
    if not keyword:
        await ctx.send(Message(msg("music_genre.rel_usage")))
        return

    client = MusicGenreClient(get_config())
    try:
        rel = client.get_relationships(keyword)
    except MusicGenreNotFound:
        await ctx.send(Message(msg("music_genre.not_found", keyword=keyword)))
        return
    except MusicGenreError as e:
        await ctx.send(Message(msg("music_genre.failed", error=e)))
        return

    if rel is None:
        await ctx.send(Message(msg("music_genre.not_found", keyword=keyword)))
        return

    lines = [f"🔗 {rel.name}（{rel.chapter}）"]
    if rel.parents:
        lines.append("")
        lines.append("⬆️ 影响来源 / 母流派：")
        for p in rel.parents:
            lines.append(f"   · {p}")
    if rel.children:
        lines.append("")
        lines.append("⬇️ 衍生分支 / 子流派：")
        for c in rel.children:
            lines.append(f"   · {c}")
    if rel.related:
        lines.append("")
        lines.append(f"🔀 相关流派：{rel.related}")
    if not rel.parents and not rel.children and not rel.related:
        lines.append("")
        lines.append("（暂无明确的流派关系数据）")

    await ctx.send(Message("\n".join(lines)))


# ─── Init ─────────────────────────────────────────────────────────────────────

get_config()
