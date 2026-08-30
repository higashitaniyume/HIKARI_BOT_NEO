"""发送 QQ 原生个人名片。

走 OneBot V11 的 `contact` 消息段（`data.type = "qq"`），由 NapCat 转成 QQ 客户端里的
原生联系人卡片，点击可直接打开对方资料页。不使用 URL / 图片 / Ark 卡片模拟。
"""

from __future__ import annotations

import logging

from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed

from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command

from .config import get_config

logger = logging.getLogger("HikariBot.ContactCard")


class InvalidQQError(ValueError):
    """QQ 号校验失败，`message_key` 指向要回复用户的提示文案。"""

    def __init__(self, message_key: str) -> None:
        super().__init__(message_key)
        self.message_key = message_key


def parse_qq(args: str, *, min_digits: int = 5, max_digits: int = 11) -> int:
    """把命令参数解析为 QQ 号；不合法时抛出 `InvalidQQError`。"""
    # 去掉所有空白，容忍复制粘贴带进来的空格/换行（如「123 456 789」）。
    text = "".join(str(args or "").split())
    if not text:
        raise InvalidQQError("contact_card.usage")
    if not text.isdecimal():
        raise InvalidQQError("contact_card.not_numeric")
    if not min_digits <= len(text) <= max_digits:
        raise InvalidQQError("contact_card.bad_length")

    # 全角数字（１２３）也满足 isdecimal，int() 会归一化成 ASCII。
    user_id = int(text)
    if user_id <= 0:
        raise InvalidQQError("contact_card.bad_length")
    return user_id


def build_contact_segment(user_id: int) -> MessageSegment:
    """构造 `{"type": "contact", "data": {"type": "qq", "id": "<QQ号>"}}` 消息段。

    当前适配器（nonebot-adapter-onebot >= 2.4.6）原生提供 `contact_user`；
    万一该工厂方法缺失，退回通用消息段构造，发给 NapCat 的 JSON 完全一致。
    """
    factory = getattr(MessageSegment, "contact_user", None)
    if callable(factory):
        return factory(int(user_id))
    return MessageSegment("contact", {"type": "qq", "id": str(int(user_id))})


@command(
    "名片",
    aliases=("个人名片", "qq名片", "card"),
    description="发送指定 QQ 的原生个人名片",
    usage="名片 QQ号",
    detail_key="contact_card.help",
    category="互动",
)
async def handle_contact_card(ctx: CommandContext) -> None:
    cfg = get_config()
    if not cfg["enabled"]:
        logger.info("[ContactCard] 插件已关闭，跳过 args=%r", ctx.args)
        return

    try:
        user_id = parse_qq(
            ctx.args,
            min_digits=cfg["min_digits"],
            max_digits=cfg["max_digits"],
        )
    except InvalidQQError as e:
        logger.info("[ContactCard] 参数校验失败 args=%r reason=%s", ctx.args, e.message_key)
        await _reply(ctx, e.message_key)
        return

    segment = build_contact_segment(user_id)
    logger.info("[ContactCard] 发送个人名片 target=%s segment=%r", user_id, segment.data)
    try:
        # ctx.send -> bot.send(event, message)，发送目标由 Event 自动判定（群聊发群、私聊发人）。
        await ctx.send(Message(segment))
    except ActionFailed as e:
        logger.warning(
            "[ContactCard] NapCat 拒绝 contact 消息段 target=%s info=%s",
            user_id,
            getattr(e, "info", e),
        )
        await _reply(ctx, "contact_card.send_failed")
    except Exception as e:
        logger.exception("[ContactCard] 发送个人名片异常 target=%s: %s", user_id, e)
        await _reply(ctx, "contact_card.send_failed")
    else:
        logger.info("[ContactCard] 个人名片已发送 target=%s", user_id)


async def _reply(ctx: CommandContext, message_key: str) -> None:
    """回复纯文本提示；连接本身有问题时只记日志，不再向上抛。"""
    try:
        await ctx.send(Message(msg(message_key)))
    except Exception as e:
        logger.warning("[ContactCard] 回复提示失败 key=%s: %s", message_key, e)
