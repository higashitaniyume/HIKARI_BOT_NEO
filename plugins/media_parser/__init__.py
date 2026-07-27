"""Aggregated media parser plugin powered by astrbot_plugin_media_parser."""

from __future__ import annotations

import logging

from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent

from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command
from core.message_pipeline import register_handler

from .bilibili_cookie_assist import bilibili_cookie_assist
from .config import get_config
from .queues import (
    MediaPrepareAttempt,
    _bilibili_cookie_login_runtime,
    _create_record_manager,
    _enqueue_text,
    _get_runtime,
    _queue_settings,
    _trigger_bilibili_cookie_assist_if_needed,
)
from .prepare import (
    _event_text,
    _limit_metadata_for_send,
    _prepare_text,
    _should_retry_prepare_result,
    is_platform_allowed,
)

logger = logging.getLogger("HikariBot.MediaParser")

get_config()

SUPPORTED_LINK_MARKERS = (
    "bilibili.com",
    "b23.tv",
    "douyin.com",
    "iesdouyin.com",
    "tiktok.com",
    "kuaishou.com",
    "gifshow.com",
    "chenzhongtech.com",
    "weibo.com",
    "weibo.cn",
    "xiaohongshu.com",
    "xhslink.com",
    "goofish.com",
    "m.tb.cn",
    "toutiao.com",
    "xiaoheihe.cn",
    "twitter.com",
    "x.com",
)


class BilibiliCookieAssistReplyHandler:
    """Consume superuser private replies for Bilibili Cookie QR login."""

    name = "BilibiliCookieAssist"

    async def match(self, event: MessageEvent, text: str) -> bool:
        return bilibili_cookie_assist.should_handle_reply(event)

    async def handle(self, bot: Bot, event: MessageEvent) -> None:
        await bilibili_cookie_assist.handle_reply(bot, event)


class AutoMediaParserHandler:
    """Automatically detect and parse supported media platform links."""

    name = "MediaParser"

    async def match(self, event: MessageEvent, text: str) -> bool:
        if bilibili_cookie_assist.should_handle_reply(event):
            return False
        parse_text = _event_text(event)
        lowered = parse_text.casefold()
        if not any(marker in lowered for marker in SUPPORTED_LINK_MARKERS):
            return False
        runtime = _get_runtime()
        if runtime is None:
            return False
        if not runtime.config_manager.trigger.should_parse(parse_text):
            return False
        links = runtime.parser_manager.extract_all_links(parse_text)
        allowed_links = [
            (url, parser) for url, parser in links
            if is_platform_allowed(getattr(parser, "name", "unknown"), event)
        ]
        return bool(allowed_links)

    async def handle(self, bot: Bot, event: MessageEvent) -> None:
        await _enqueue_text(bot, event, _event_text(event))


@command(
    "媒体解析",
    aliases=("解析媒体", "视频解析"),
    description="解析抖音/B站/小红书/小黑盒等平台链接",
    usage="媒体解析 <链接>",
)
async def media_parse_command(ctx: CommandContext) -> None:
    if not ctx.args:
        await ctx.send(Message(msg("media_parser.usage")))
        return
    await _enqueue_text(ctx.bot, ctx.event, ctx.args, force=True)


@command(
    "B站登录",
    aliases=("B站Cookie", "刷新B站Cookie", "b站登录", "bilibili登录"),
    description="向超级管理员私发 B站扫码登录二维码",
    usage="B站登录",
    show_in_help=False,
)
async def bilibili_cookie_login_command(ctx: CommandContext) -> None:
    if not bilibili_cookie_assist.is_superuser_event(ctx.event):
        await ctx.send(Message(msg("media_parser.bilibili_cookie_assist_permission_denied")))
        return

    runtime_parts = _bilibili_cookie_login_runtime()
    if runtime_parts is None:
        await ctx.send(Message(msg("media_parser.bilibili_cookie_assist_manual_unavailable")))
        return

    parser, bili_cfg = runtime_parts
    started = await bilibili_cookie_assist.start_manual_login(
        ctx.bot,
        auth_runtime=parser.get_auth_runtime(),
        reply_timeout_minutes=bili_cfg.admin_reply_timeout_minutes,
    )
    if started and isinstance(ctx.event, GroupMessageEvent):
        await ctx.send(Message(msg("media_parser.bilibili_cookie_assist_manual_started")))


register_handler(BilibiliCookieAssistReplyHandler())
register_handler(AutoMediaParserHandler())
logger.info("Aggregated media parser registered")
