"""机器人所在群列表（后台挑选主动接话群用）。"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("HikariBot.BotAdmin.Groups")


def group_list_state() -> dict[str, Any]:
    """同步入口：借机器人主循环调 OneBot `get_group_list`。"""
    from plugins.astrbot_compat.runtime import submit_coroutine

    groups = submit_coroutine(_fetch_groups(), timeout=20.0)
    return {"groups": groups}


async def _fetch_groups() -> list[dict[str, Any]]:
    import nonebot

    bots = nonebot.get_bots()
    bot = next(iter(bots.values()), None) if bots else None
    if bot is None:
        raise RuntimeError("机器人当前未连接，无法读取群列表。")

    data = await bot.call_api("get_group_list")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        group_id = str(item.get("group_id") or "").strip()
        if not group_id.isdigit() or group_id in seen:
            continue
        seen.add(group_id)
        name = str(item.get("group_name") or "").strip() or group_id
        rows.append(
            {
                "id": group_id,
                "name": name[:80],
                "member_count": _safe_int(item.get("member_count")),
            }
        )
    rows.sort(key=lambda row: (row["name"].casefold(), row["id"]))
    return rows


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
