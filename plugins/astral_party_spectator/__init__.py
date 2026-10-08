"""吉星派对（Astral Party）官方 PVE 观战手牌查询。

命令：`查询 <观战码>`，向 AstralPartySpectator 服务请求房间快照，把服务器
实际下发的四名玩家手牌以文本形式发回群聊/私聊。
"""

from __future__ import annotations

import logging

from nonebot.adapters.onebot.v11 import Message

from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command

from .api import (
    SpectatorClient,
    SpectatorError,
    SpectatorNotAvailable,
    SpectatorTimeout,
    SpectatorUnavailable,
)
from .config import get_config
from .formatting import format_hand_message
from .models import SpectatorSnapshot

logger = logging.getLogger("HikariBot.AstralPartySpectator")


def _enabled() -> bool:
    return bool(get_config().get("enabled", True))


def _get_client() -> SpectatorClient:
    return SpectatorClient(get_config())


async def _query_snapshot(code: str) -> SpectatorSnapshot:
    return await _get_client().query(code)


def _render(snapshot: SpectatorSnapshot, code: str) -> str:
    cfg = get_config()
    return format_hand_message(
        snapshot,
        watch_code=code,
        max_chars=int(cfg.get("max_chars") or 3000),
        max_name_chars=int(cfg.get("max_name_chars") or 40),
        max_description_chars=int(cfg.get("max_description_chars") or 60),
    )


@command(
    "查询",
    aliases=("观战", "手牌"),
    description="查询吉星派对观战码，显示四名玩家的手牌",
    usage="查询 <观战码>",
    category="游戏",
)
async def handle_spectator_query(ctx: CommandContext) -> None:
    if not _enabled():
        return

    code = ctx.args.strip()
    if not code:
        await ctx.send(Message(msg("astral_party.usage")))
        return

    try:
        snapshot = await _query_snapshot(code)
    except SpectatorNotAvailable as e:
        logger.info("[AstralParty] 观战码不可用 code=%s server_error=%s", code, e.server_error)
        await ctx.send(Message(msg("astral_party.not_available", reason=str(e))))
        return
    except SpectatorTimeout as e:
        logger.warning("[AstralParty] 查询超时 code=%s error=%s", code, e)
        await ctx.send(Message(msg("astral_party.timeout", error=str(e))))
        return
    except SpectatorUnavailable as e:
        logger.warning("[AstralParty] 服务不可用 code=%s error=%s", code, e)
        await ctx.send(Message(msg("astral_party.unavailable", error=str(e))))
        return
    except SpectatorError as e:
        logger.warning("[AstralParty] 查询失败 code=%s error=%s", code, e)
        await ctx.send(Message(msg("astral_party.failed", error=str(e))))
        return

    await ctx.send(Message(_render(snapshot, code)))


get_config()

__all__ = ["handle_spectator_query"]
