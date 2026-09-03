"""撤回相关的 OneBot 调用封装。"""

from __future__ import annotations

import logging
from typing import Any

from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11.exception import ActionFailed

logger = logging.getLogger("HikariBot.GroupGuard")

_ADMIN_ROLES = {"owner", "admin"}


async def delete_message(bot: Bot, message_id: int, *, context: str = "") -> bool:
    """撤回一条消息。撤回窗口过期、权限不足等失败情况都返回 False。"""
    try:
        await bot.delete_msg(message_id=int(message_id))
    except ActionFailed as e:
        logger.warning(
            "[GroupGuard] 撤回失败 message_id=%s context=%s info=%s",
            message_id,
            context or "-",
            getattr(e, "info", e),
        )
        return False
    except Exception as e:
        logger.exception("[GroupGuard] 撤回异常 message_id=%s context=%s: %s", message_id, context, e)
        return False
    logger.info("[GroupGuard] 已撤回 message_id=%s context=%s", message_id, context or "-")
    return True


async def group_member_role(bot: Bot, group_id: Any, user_id: Any) -> str:
    """返回群成员角色（owner / admin / member），查询失败返回空串。"""
    try:
        info = await bot.get_group_member_info(
            group_id=int(group_id),
            user_id=int(user_id),
            no_cache=False,
        )
    except ActionFailed as e:
        logger.warning(
            "[GroupGuard] 查询群成员角色失败 group=%s user=%s info=%s",
            group_id,
            user_id,
            getattr(e, "info", e),
        )
        return ""
    except Exception as e:
        logger.exception("[GroupGuard] 查询群成员角色异常 group=%s user=%s: %s", group_id, user_id, e)
        return ""
    return str((info or {}).get("role") or "").strip().casefold()


async def is_group_admin(bot: Bot, group_id: Any, user_id: Any) -> bool:
    return await group_member_role(bot, group_id, user_id) in _ADMIN_ROLES
