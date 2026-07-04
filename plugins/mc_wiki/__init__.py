from __future__ import annotations

import logging

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment

from core.ai_tool_registry import AIToolContext, register_ai_tool
from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command

from .api import McWikiClient, McWikiError, McWikiNotFound, McWikiResult
from .config import get_config

logger = logging.getLogger("HikariBot.McWiki")


def _enabled() -> bool:
    return bool(get_config().get("enabled", True))


def _format_link(result: McWikiResult) -> str:
    return msg("mc_wiki.link", title=result.title, url=result.url)


def _format_detail(result: McWikiResult) -> str:
    return msg("mc_wiki.detail", title=result.title, detail=result.detail)


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
                "detail": result.detail,
                "url": result.url,
                "image_url": result.image_url,
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
        await _send_result(ctx, result)
    except McWikiNotFound:
        await ctx.send(Message(msg("mc_wiki.not_found", keyword=keyword)))
    except McWikiError as e:
        logger.warning("[McWiki] 查询失败 keyword=%r error=%s", keyword, e)
        await ctx.send(Message(msg("mc_wiki.failed", error=e)))


async def _send_result(ctx: CommandContext, result: McWikiResult) -> None:
    nodes = _build_forward_nodes(ctx.bot.self_id, result)
    try:
        await _send_forward(ctx, nodes)
    except Exception as e:
        logger.warning("[McWiki] 合并转发失败 title=%r error=%s", result.title, e)
        await _send_separate(ctx, result)


def _build_forward_nodes(self_id: str, result: McWikiResult) -> list[MessageSegment]:
    nodes = [
        _node(self_id, Message(_format_link(result))),
        _node(self_id, Message(_format_detail(result))),
    ]
    image_message = _build_image_message(result)
    if image_message is not None:
        nodes.append(_node(self_id, image_message))
    return nodes


def _node(self_id: str, content: Message) -> MessageSegment:
    return MessageSegment.node_custom(
        user_id=int(self_id),
        nickname="HIKARI",
        content=content,
    )


def _build_image_message(result: McWikiResult) -> Message | None:
    if not result.image_url:
        return None
    return Message(msg("mc_wiki.image_caption", title=result.title) + "\n") + MessageSegment.image(result.image_url)


async def _send_forward(ctx: CommandContext, nodes: list[MessageSegment]) -> None:
    if isinstance(ctx.event, GroupMessageEvent):
        await ctx.bot.send_group_forward_msg(group_id=ctx.event.group_id, messages=nodes)
        return
    await ctx.bot.send_private_forward_msg(user_id=int(ctx.event.get_user_id()), messages=nodes)


async def _send_separate(ctx: CommandContext, result: McWikiResult) -> None:
    await ctx.send(Message(_format_link(result)))
    await ctx.send(Message(_format_detail(result)))
    image_message = _build_image_message(result)
    if image_message is None:
        return
    try:
        await ctx.send(image_message)
    except Exception as e:
        logger.warning("[McWiki] 主图发送失败 title=%r error=%s", result.title, e)


get_config()
