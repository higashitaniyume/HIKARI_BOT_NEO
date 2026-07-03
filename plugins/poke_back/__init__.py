"""Poke back when someone pokes the bot."""

from __future__ import annotations

import logging
from typing import Any

from nonebot import on_notice
from nonebot.adapters.onebot.v11 import Bot, NoticeEvent, PokeNotifyEvent
from nonebot.adapters.onebot.v11.exception import ActionFailed

from .config import get_config

logger = logging.getLogger("HikariBot.PokeBack")

poke_back_matcher = on_notice(priority=20, block=False)


@poke_back_matcher.handle()
async def handle_poke_back(bot: Bot, event: NoticeEvent) -> None:
    if not isinstance(event, PokeNotifyEvent):
        return

    cfg = get_config()
    group_id = getattr(event, "group_id", None)
    if not should_poke_back(
        actor_id=int(event.user_id),
        target_id=int(event.target_id),
        self_id=int(bot.self_id),
        group_id=int(group_id) if group_id else None,
        config=cfg,
    ):
        return

    try:
        await send_poke(bot, user_id=int(event.user_id), group_id=int(group_id) if group_id else None)
    except ActionFailed as e:
        logger.warning(
            "[PokeBack] 戳回失败 user_id=%s group_id=%s info=%s",
            event.user_id,
            group_id,
            getattr(e, "info", e),
        )


async def send_poke(bot: Any, *, user_id: int, group_id: int | None = None) -> Any:
    data: dict[str, int] = {"user_id": user_id}
    if group_id is not None:
        data["group_id"] = group_id
    return await bot.call_api("send_poke", **data)


def should_poke_back(
    *,
    actor_id: int,
    target_id: int,
    self_id: int,
    group_id: int | None,
    config: dict[str, Any],
) -> bool:
    if not bool(config.get("enabled", True)):
        return False
    if target_id != self_id:
        return False
    if actor_id == self_id:
        return False
    if group_id is None:
        return bool(config.get("private_enabled", True))
    return bool(config.get("group_enabled", True))
