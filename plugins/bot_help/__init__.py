"""机器人帮助信息插件。"""

from __future__ import annotations

from nonebot.adapters.onebot.v11 import Message

from core.bot_messages import get_message as msg
from core.command_router import CommandContext, CommandSpec, command, iter_commands


def _command_scope(spec: CommandSpec) -> str:
    scopes: list[str] = []
    if spec.private_only:
        scopes.append(msg("bot_help.scope_private"))
    if spec.group_only:
        scopes.append(msg("bot_help.scope_group"))
    if spec.require_tome:
        scopes.append(msg("bot_help.scope_tome"))
    return "；".join(scopes)


def _format_command_line(spec: CommandSpec) -> str:
    usage = spec.usage or spec.name
    description = msg("bot_help.command_description", description=spec.description) if spec.description else ""
    scope = _command_scope(spec)
    scope_text = msg("bot_help.command_scope", scope=scope) if scope else ""
    return msg("bot_help.command_line", usage=usage, description=description, scope=scope_text)


def _unique_commands() -> list[CommandSpec]:
    commands: list[CommandSpec] = []
    seen: set[str] = set()
    for spec in iter_commands():
        if spec.name in seen:
            continue
        seen.add(spec.name)
        commands.append(spec)
    return commands


def _find_command(name: str) -> CommandSpec | None:
    normalized = name.strip().casefold()
    if not normalized:
        return None
    for spec in _unique_commands():
        names = (spec.name, *spec.aliases)
        if any(candidate.casefold() == normalized for candidate in names):
            return spec
    return None


def _format_command_list() -> str:
    commands = _unique_commands()
    if not commands:
        return msg("bot_help.command_list_empty")
    return "\n".join([msg("bot_help.command_list_header"), *[_format_command_line(spec) for spec in commands]])


def _format_command_detail(spec: CommandSpec) -> str:
    lines = [
        msg("bot_help.command_detail_name", name=spec.name),
        msg("bot_help.command_detail_usage", usage=spec.usage or spec.name),
    ]
    if spec.description:
        lines.append(msg("bot_help.command_detail_description", description=spec.description))
    if spec.aliases:
        lines.append(msg("bot_help.command_detail_aliases", aliases=", ".join(spec.aliases)))
    scope = _command_scope(spec)
    if scope:
        lines.append(msg("bot_help.command_detail_scope", scope=scope))
    return "\n".join(lines)


def _summary_help() -> str:
    blocks = [
        [msg("bot_help.summary_title")],
        _format_command_list().splitlines(),
        msg("bot_help.fallback").splitlines(),
        msg("bot_help.web").splitlines(),
        [msg("bot_help.summary_more")],
    ]
    return "\n\n".join("\n".join(block) for block in blocks if block)


def _full_help() -> str:
    blocks = [
        [msg("bot_help.full_title")],
        _format_command_list().splitlines(),
        msg("bot_help.auto_parse").splitlines(),
        msg("bot_help.fallback").splitlines(),
        msg("bot_help.web").splitlines(),
        msg("bot_help.usage").splitlines(),
    ]
    return "\n\n".join("\n".join(block) for block in blocks if block)


@command("帮助", aliases=("/help", "help", "菜单"), description="查看帮助", usage="帮助 [命令|全部]", require_tome=True)
async def handle_help(ctx: CommandContext) -> None:
    arg = ctx.args.strip()
    if not arg:
        await ctx.send(Message(_summary_help()))
        return

    if arg.casefold() in {"全部", "all", "full"}:
        await ctx.send(Message(_full_help()))
        return

    if arg.casefold() in {"命令", "commands", "command"}:
        await ctx.send(Message(_format_command_list()))
        return

    spec = _find_command(arg)
    if spec is None:
        await ctx.send(Message(msg("bot_help.not_found", command=arg)))
        return

    await ctx.send(Message(_format_command_detail(spec)))
