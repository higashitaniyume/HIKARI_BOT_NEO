from __future__ import annotations

import logging

from nonebot.adapters.onebot.v11 import Message

from core.command_router import CommandContext, command

from .api import StardewWikiClient, StardewWikiError, StardewWikiNotFound
from .config import get_config

logger = logging.getLogger("HikariBot.StardewWiki")


def _enabled() -> bool:
    return bool(get_config().get("enabled", True))


def _format_result(title: str, summary: str, url: str) -> str:
    return "\n".join(
        [
            f"标题：{title}",
            f"简介：{summary}",
            f"URL：{url}",
        ]
    )


@command(
    "星露谷wiki",
    aliases=(
        "星露谷Wiki",
        "星露谷物语wiki",
        "星露谷维基",
        "星露谷",
        "/svwiki",
        "svwiki",
        "sdvwiki",
        "stardewwiki",
    ),
    description="搜索星露谷物语中文 Wiki",
    usage="星露谷wiki <关键词>",
    require_tome=True,
)
async def handle_stardew_wiki(ctx: CommandContext) -> None:
    if not _enabled():
        return

    keyword = ctx.args.strip()
    if not keyword:
        await ctx.send(Message("用法：星露谷wiki <关键词>"))
        return

    try:
        result = await StardewWikiClient(get_config()).search(keyword)
        await ctx.send(Message(_format_result(result.title, result.summary, result.url)))
    except StardewWikiNotFound:
        await ctx.send(Message(f"没有在星露谷物语 Wiki 找到「{keyword}」。"))
    except StardewWikiError as e:
        logger.warning("[StardewWiki] 查询失败 keyword=%r error=%s", keyword, e)
        await ctx.send(Message(f"星露谷物语 Wiki 查询失败：{e}"))


get_config()
