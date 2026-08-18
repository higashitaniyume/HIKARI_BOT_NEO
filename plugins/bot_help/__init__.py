"""机器人帮助信息插件。"""

from __future__ import annotations

from nonebot.adapters.onebot.v11 import Message

from core.bot_messages import get_message as msg
from core.command_router import CommandContext, CommandSpec, command, iter_commands

# 分区的优先展示顺序；未列出的分区追加在末尾
_PREFERRED_CATEGORY_ORDER = ("基础", "贴纸", "媒体", "语音", "互动", "资讯", "订阅", "音乐", "游戏", "百科", "管理")

# 分区详情末尾附带的额外说明块（对应 core/bot_messages.py 的键）
_CATEGORY_EXTRA_BLOCKS = {
    "媒体": "bot_help.auto_parse",
    "管理": "bot_help.web",
}


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
    description = msg("bot_help.command_description", description=spec.description) if spec.description else ""
    return msg(
        "bot_help.command_line",
        name=spec.name,
        usage=spec.name,
        description=description,
        scope="",
    )


def _unique_commands(*, public_only: bool = False) -> list[CommandSpec]:
    commands: list[CommandSpec] = []
    seen: set[str] = set()
    for spec in iter_commands():
        if public_only and not spec.show_in_help:
            continue
        if spec.name in seen:
            continue
        seen.add(spec.name)
        commands.append(spec)
    return commands


def _find_command(name: str) -> CommandSpec | None:
    normalized = name.strip().casefold()
    if not normalized:
        return None
    for spec in _unique_commands(public_only=True):
        names = (spec.name, *spec.aliases)
        if any(candidate.casefold() == normalized for candidate in names):
            return spec
    return None


def _format_command_list(specs: list[CommandSpec] | None = None) -> str:
    if specs is None:
        specs = _unique_commands(public_only=True)
    if not specs:
        return msg("bot_help.command_list_empty")
    return "\n".join([msg("bot_help.command_list_header"), *[_format_command_line(spec) for spec in specs]])


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
    if spec.detail_key:
        lines.append(msg("bot_help.command_detail_extra", details=msg(spec.detail_key)))
    return "\n".join(lines)


def _grouped_commands() -> list[tuple[str, list[CommandSpec]]]:
    by_category: dict[str, list[CommandSpec]] = {}
    for spec in _unique_commands(public_only=True):
        name = spec.category or msg("bot_help.category_default")
        by_category.setdefault(name, []).append(spec)
    ordered = [name for name in _PREFERRED_CATEGORY_ORDER if name in by_category]
    ordered.extend(name for name in by_category if name not in ordered)
    return [(name, by_category[name]) for name in ordered]


def _find_category(name: str) -> str | None:
    normalized = name.strip().casefold()
    if not normalized:
        return None
    for category_name, _ in _grouped_commands():
        if category_name.casefold() == normalized:
            return category_name
    return None


def _format_category_index() -> str:
    blocks: list[list[str]] = [[msg("bot_help.summary_title")], []]
    for name, _ in _grouped_commands():
        if name == "媒体":
            platforms = msg("bot_help.category_media_platforms")
            blocks.append([msg("bot_help.category_line_with_platforms", name=name, platforms=platforms)])
        else:
            blocks.append([msg("bot_help.category_line", name=name)])
    blocks.append([msg("bot_help.category_hint")])
    return "\n".join("\n".join(block) for block in blocks)


def _format_category_detail(name: str) -> str:
    by_category = dict(_grouped_commands())
    specs = by_category.get(name)
    if specs is None:
        return msg("bot_help.category_not_found", category=name)

    blocks: list[list[str]] = [[msg("bot_help.category_detail_title", name=name)], []]
    blocks.append(_format_command_list(specs).splitlines())
    extra_key = _CATEGORY_EXTRA_BLOCKS.get(name)
    if extra_key:
        blocks.append(msg(extra_key).splitlines())
    blocks.append([msg("bot_help.category_detail_hint")])
    return "\n".join("\n".join(block) for block in blocks)


def _format_full_help() -> str:
    blocks: list[list[str]] = [[msg("bot_help.full_title")], []]
    for name, specs in _grouped_commands():
        blocks.append([msg("bot_help.category_detail_title", name=name)])
        blocks.append(_format_command_list(specs).splitlines())
        extra_key = _CATEGORY_EXTRA_BLOCKS.get(name)
        if extra_key:
            blocks.append(msg(extra_key).splitlines())
        blocks.append([])
    blocks.append(msg("bot_help.usage").splitlines())
    return "\n".join("\n".join(block) for block in blocks)


def _resolve_help(arg: str) -> str:
    arg = arg.strip()
    if not arg:
        return _format_category_index()

    if arg.casefold() in {"全部", "all", "full"}:
        return _format_full_help()

    if arg.casefold() in {"命令", "commands", "command"}:
        return _format_command_list()

    spec = _find_command(arg)
    if spec is not None:
        return _format_command_detail(spec)

    category = _find_category(arg)
    if category is not None:
        return _format_category_detail(category)

    return msg("bot_help.not_found", command=arg)


@command("帮助", aliases=("/help", "help", "菜单"), description="查看帮助", usage="帮助 [分区|命令|全部]", require_tome=True, category="基础")
async def handle_help(ctx: CommandContext) -> None:
    await ctx.send(Message(_resolve_help(ctx.args)))
