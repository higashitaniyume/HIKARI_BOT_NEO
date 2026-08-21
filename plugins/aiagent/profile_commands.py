"""AI Agent 多配置文件的聊天命令。

三条命令都是 superuser_only：切换配置会直接改变调用的模型和 API Key，
也就直接影响花费；core/command_router.py 目前只有「超级用户」这一档权限，
要放宽到群管需要先给 CommandSpec 加新的权限标志。
"""

from __future__ import annotations

import logging

from nonebot.adapters.onebot.v11 import Message

from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command

from .config import (
    binding_scope,
    clear_binding,
    find_profile_id,
    get_raw_config,
    list_profiles,
    resolve_profile_id,
    set_binding,
)

logger = logging.getLogger("HikariBot.AIAgent.Profiles")

_KIND_LABELS = {"group": "本群", "private": "当前私聊"}


@command(
    "AI配置列表",
    aliases=("配置列表",),
    description="查看 AI Agent 的全部配置文件",
    usage="AI配置列表",
    category="管理",
    superuser_only=True,
)
async def handle_profile_list(ctx: CommandContext) -> None:
    """列出所有配置文件，标出全局默认和当前会话正在用的那个。"""
    doc = get_raw_config()
    kind, ident = binding_scope(ctx.event)
    current = resolve_profile_id(kind, ident, doc=doc)

    lines = [msg("aiagent.profile_list_header")]
    for profile in list_profiles(doc):
        marks = []
        if profile["is_active"]:
            marks.append("全局默认")
        if profile["id"] == current:
            marks.append("当前生效")
        if profile["bound_count"]:
            marks.append(f"已绑定 {profile['bound_count']} 个会话")
        lines.append(
            msg(
                "aiagent.profile_list_item",
                name=profile["name"],
                id=profile["id"],
                model=profile["model"] or "(未设置模型)",
                marks=("　" + " / ".join(marks)) if marks else "",
            )
        )
    await ctx.send(Message("\n".join(lines)))


@command(
    "切换AI配置",
    description="把当前会话绑定到指定 AI 配置文件",
    usage="切换AI配置 <名称|ID>",
    category="管理",
    superuser_only=True,
)
async def handle_profile_switch(ctx: CommandContext) -> None:
    """把当前群 / 私聊绑定到指定配置文件。"""
    keyword = ctx.args.strip()
    if not keyword:
        await ctx.send(Message(msg("aiagent.profile_switch_usage")))
        return

    doc = get_raw_config()
    profile_id = find_profile_id(keyword, doc=doc)
    if profile_id is None:
        await ctx.send(Message(msg("aiagent.profile_not_found", keyword=keyword)))
        return

    kind, ident = binding_scope(ctx.event)
    set_binding(kind, ident, profile_id)
    logger.info("[AIAgent] %s:%s 绑定到配置 %s", kind, ident, profile_id)
    await ctx.send(
        Message(
            msg(
                "aiagent.profile_switched",
                scope=_KIND_LABELS.get(kind, "当前会话"),
                name=doc["profiles"][profile_id].get("name") or profile_id,
            )
        )
    )


@command(
    "解绑AI配置",
    description="清除当前会话的 AI 配置绑定，回落默认配置",
    usage="解绑AI配置",
    category="管理",
    superuser_only=True,
)
async def handle_profile_unbind(ctx: CommandContext) -> None:
    """清除当前会话绑定，回落到全局默认配置文件。"""
    kind, ident = binding_scope(ctx.event)
    clear_binding(kind, ident)
    doc = get_raw_config()
    default_id = doc["active_profile"]
    logger.info("[AIAgent] %s:%s 已解绑 AI 配置", kind, ident)
    await ctx.send(
        Message(
            msg(
                "aiagent.profile_unbound",
                scope=_KIND_LABELS.get(kind, "当前会话"),
                name=doc["profiles"][default_id].get("name") or default_id,
            )
        )
    )
