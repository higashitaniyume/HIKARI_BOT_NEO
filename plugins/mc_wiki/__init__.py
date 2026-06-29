from __future__ import annotations

import logging

from nonebot.adapters.onebot.v11 import Message

from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command

from .api import McWikiClient, McWikiError, McWikiNotFound
from .config import get_config

logger = logging.getLogger("HikariBot.McWiki")


def _enabled() -> bool:
    return bool(get_config().get("enabled", True))


def _format_result(title: str, summary: str, url: str) -> str:
    return msg("mc_wiki.result", title=title, summary=summary, url=url)


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
