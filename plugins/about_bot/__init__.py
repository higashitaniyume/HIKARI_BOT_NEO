"""机器人关于信息插件。"""

from __future__ import annotations

from nonebot.adapters.onebot.v11 import Message

from core.bot_identity import get_bot_name
from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command
from core.runtime_info import format_duration, get_runtime_info, get_uptime_seconds
from plugins import sticker_library


@command("关于", aliases=("about",), description="查看机器人信息", usage="关于", require_tome=True)
async def handle_about(ctx: CommandContext) -> None:
    state = sticker_library.get_state()
    runtime = get_runtime_info()

    await ctx.send(
        Message(
            msg(
                "about.response",
                name=get_bot_name(),
                description=msg("about.description"),
                version=runtime.version,
                git_hash=runtime.git_hash,
                title=runtime.title,
                uptime=format_duration(get_uptime_seconds()),
                total_stickers=state.get("total_stickers", 0),
                pack_count=len(state.get("packs") or []),
                keyword_count=len(state.get("keywords") or []),
            )
        )
    )
