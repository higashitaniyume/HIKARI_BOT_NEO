from __future__ import annotations

import logging

from nonebot.adapters.onebot.v11 import Message

from core.ai_tool_registry import AIToolContext, register_ai_tool
from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command

from .api import StardewWikiClient, StardewWikiError, StardewWikiNotFound
from .config import get_config

logger = logging.getLogger("HikariBot.StardewWiki")


def _enabled() -> bool:
    return bool(get_config().get("enabled", True))


def _format_result(title: str, summary: str, url: str) -> str:
    return msg("stardew_wiki.result", title=title, summary=summary, url=url)


@register_ai_tool(
    "stardew_wiki_search",
    plugin_name="stardew_wiki",
    description="Search the Chinese Stardew Valley Wiki and return the best matching page summary and URL.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Stardew Valley Wiki search keyword.",
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)
async def ai_tool_stardew_wiki_search(context: AIToolContext, arguments: dict[str, object]) -> dict[str, object]:
    if not _enabled():
        return {"error": "stardew_wiki is disabled"}
    keyword = str(arguments.get("query") or "").strip()
    if not keyword:
        return {"error": "query is required"}
    try:
        result = await StardewWikiClient(get_config()).search(keyword)
    except StardewWikiNotFound:
        return {"query": keyword, "not_found": True, "results": []}
    except StardewWikiError as e:
        logger.warning("[StardewWiki] AI Tool 查询失败 keyword=%r error=%s", keyword, e)
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
    "星露谷wiki",
    aliases=(
        "星露谷Wiki",
        "星露谷物语wiki",
        "星露谷维基",
        "星露谷",
        "svwiki",
        "sdvwiki",
        "stardewwiki",
    ),
    description="搜索星露谷物语中文 Wiki",
    usage="星露谷wiki <关键词>",
)
async def handle_stardew_wiki(ctx: CommandContext) -> None:
    if not _enabled():
        return

    keyword = ctx.args.strip()
    if not keyword:
        await ctx.send(Message(msg("stardew_wiki.usage")))
        return

    try:
        result = await StardewWikiClient(get_config()).search(keyword)
        await ctx.send(Message(_format_result(result.title, result.summary, result.url)))
    except StardewWikiNotFound:
        await ctx.send(Message(msg("stardew_wiki.not_found", keyword=keyword)))
    except StardewWikiError as e:
        logger.warning("[StardewWiki] 查询失败 keyword=%r error=%s", keyword, e)
        await ctx.send(Message(msg("stardew_wiki.failed", error=e)))


get_config()
