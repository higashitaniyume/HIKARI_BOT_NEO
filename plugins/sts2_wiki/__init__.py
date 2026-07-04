from __future__ import annotations

import logging

from nonebot.adapters.onebot.v11 import Message

from core.ai_tool_registry import AIToolContext, register_ai_tool
from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command

from .api import Sts2WikiError, Sts2WikiNotFound
from .config import get_config
from .models import Sts2WikiResult
from .service import (
    Sts2WikiKeywordEmpty,
    Sts2WikiKeywordTooLong,
    Sts2WikiService,
    normalize_keyword,
)

logger = logging.getLogger("HikariBot.Sts2Wiki")


def _enabled() -> bool:
    return bool(get_config().get("enabled", True))


@register_ai_tool(
    "sts2_wiki_search",
    plugin_name="sts2_wiki",
    description="Search wiki.gg's Slay the Spire Wiki for Slay the Spire 2 entries and return the best matching page summary and URL.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Slay the Spire 2 Wiki search keyword.",
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)
async def ai_tool_sts2_wiki_search(context: AIToolContext, arguments: dict[str, object]) -> dict[str, object]:
    cfg = get_config()
    if not bool(cfg.get("enabled", True)):
        return {"error": "sts2_wiki is disabled"}

    max_chars = max(1, int(cfg.get("query_max_chars") or 80))
    try:
        keyword = normalize_keyword(str(arguments.get("query") or ""), max_chars=max_chars)
    except Sts2WikiKeywordEmpty:
        return {"error": "query is required"}
    except Sts2WikiKeywordTooLong as e:
        return {"error": f"query must be at most {e.max_chars} characters"}

    try:
        result = await Sts2WikiService(cfg).lookup(keyword)
    except Sts2WikiNotFound:
        return {"query": keyword, "not_found": True, "results": []}
    except Sts2WikiError as e:
        logger.warning("[Sts2Wiki] AI Tool 查询失败 keyword=%r error=%s", keyword, e)
        return {"query": keyword, "error": str(e)}
    except Exception as e:
        logger.exception("[Sts2Wiki] AI Tool 查询异常 keyword=%r error=%s", keyword, e)
        return {"query": keyword, "error": type(e).__name__}

    return {
        "query": keyword,
        "cache_hit": result.cache_hit,
        "results": [
            {
                "title": result.title,
                "summary": result.summary,
                "extract": result.extract,
                "url": result.url,
                "updated_at": result.updated_at,
            }
        ],
        "candidates": [
            {"title": candidate.title, "snippet": candidate.snippet}
            for candidate in result.candidates[:5]
        ],
    }


@command(
    "塔2wiki",
    aliases=(
        "塔2Wiki",
        "塔2维基",
        "塔2",
        "sts2",
        "STS2",
        "sts2wiki",
        "STS2Wiki",
    ),
    description="搜索杀戮尖塔 2 Wiki",
    usage="塔2wiki <关键词>",
)
async def handle_sts2_wiki(ctx: CommandContext) -> None:
    cfg = get_config()
    if not bool(cfg.get("enabled", True)):
        return

    max_chars = max(1, int(cfg.get("query_max_chars") or 80))
    try:
        keyword = normalize_keyword(ctx.args, max_chars=max_chars)
    except Sts2WikiKeywordEmpty:
        await ctx.send(Message(msg("sts2_wiki.usage")))
        return
    except Sts2WikiKeywordTooLong as e:
        await ctx.send(Message(msg("sts2_wiki.too_long", max_chars=e.max_chars)))
        return

    try:
        result = await Sts2WikiService(cfg).lookup(keyword)
    except Sts2WikiNotFound:
        await ctx.send(Message(msg("sts2_wiki.not_found", keyword=keyword)))
        return
    except Sts2WikiError as e:
        logger.warning("[Sts2Wiki] 查询失败 keyword=%r error=%s", keyword, e)
        await ctx.send(Message(msg("sts2_wiki.failed")))
        return
    except Exception as e:
        logger.exception("[Sts2Wiki] 查询异常 keyword=%r error=%s", keyword, e)
        await ctx.send(Message(msg("sts2_wiki.failed")))
        return

    await ctx.send(Message(format_result_message(result)))


def format_result_message(result: Sts2WikiResult) -> str:
    return msg(
        "sts2_wiki.result",
        title=result.title,
        summary=result.summary,
        url=result.url,
    )
