from __future__ import annotations

import logging

from nonebot.adapters.onebot.v11 import Message

from core.ai_tool_registry import AIToolContext, register_ai_tool
from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command

from .api import McWikiClient, McWikiError, McWikiNotFound
from .config import get_config

logger = logging.getLogger("HikariBot.McWiki")


def _enabled() -> bool:
    return bool(get_config().get("enabled", True))


def _format_result(title: str, summary: str, url: str) -> str:
    return msg("mc_wiki.result", title=title, summary=summary, url=url)


@register_ai_tool(
    "mc_wiki_search",
    plugin_name="mc_wiki",
    description="Search the Chinese Minecraft Wiki and return the best matching page summary and URL.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Minecraft Wiki search keyword.",
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)
async def ai_tool_mc_wiki_search(context: AIToolContext, arguments: dict[str, object]) -> dict[str, object]:
    if not _enabled():
        return {"error": "mc_wiki is disabled"}
    keyword = str(arguments.get("query") or "").strip()
    if not keyword:
        return {"error": "query is required"}
    try:
        result = await McWikiClient(get_config()).search(keyword)
    except McWikiNotFound:
        return {"query": keyword, "not_found": True, "results": []}
    except McWikiError as e:
        logger.warning("[McWiki] AI Tool 查询失败 keyword=%r error=%s", keyword, e)
        return {"query": keyword, "error": str(e)}
    return {
        "query": keyword,
        "results": [
            {
                "title": result.title,
                "summary": result.summary,
                "url": result.url,
            }
        ],
    }


@command(
    "mcwiki",
    aliases=(
        "MCWiki",
        "minecraftwiki",
        "MinecraftWiki",
        "我的世界wiki",
        "我的世界Wiki",
        "我的世界维基",
        "mc维基",
        "MC维基",
        "mc百科",
        "MC百科",
    ),
    description="搜索中文 Minecraft Wiki",
    usage="mcwiki <关键词>",
)
async def handle_mc_wiki(ctx: CommandContext) -> None:
    if not _enabled():
        return

    keyword = ctx.args.strip()
    if not keyword:
        await ctx.send(Message(msg("mc_wiki.usage")))
        return

    try:
        result = await McWikiClient(get_config()).search(keyword)
        await ctx.send(Message(_format_result(result.title, result.summary, result.url)))
    except McWikiNotFound:
        await ctx.send(Message(msg("mc_wiki.not_found", keyword=keyword)))
    except McWikiError as e:
        logger.warning("[McWiki] 查询失败 keyword=%r error=%s", keyword, e)
        await ctx.send(Message(msg("mc_wiki.failed", error=e)))


get_config()
