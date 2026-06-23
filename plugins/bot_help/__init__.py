"""机器人帮助信息插件。"""

from __future__ import annotations

from nonebot.adapters.onebot.v11 import Message

from core.command_router import CommandContext, CommandSpec, command, iter_commands


AUTO_PARSE_HELP = [
    "自动解析：",
    "- Pixiv 作品链接：解析并发送图片",
    "- Instagram / Facebook 链接：解析并发送媒体",
    "- Telegram 贴纸包链接：转 GIF 并发送",
    "  参数：zip / refresh / nosave / name=关键词",
]

FALLBACK_HELP = [
    "自然触发：",
    "- 贴纸关键词：随机发送一张贴纸",
    "- 贴纸关键词 10：随机发送 10 张，不重复",
]

WEB_HELP = [
    "管理页面：",
    "- https://stickers-hikari.vlnc.top/",
    "- 可上传贴纸、导入 Telegram 贴纸包、管理关键词",
]

USAGE_HELP = [
    "帮助用法：",
    "- 帮助：查看摘要",
    "- 帮助 命令：查看命令列表",
    "- 帮助 全部：查看完整说明",
    "- 帮助 <命令名>：查看单个命令",
]


def _command_scope(spec: CommandSpec) -> str:
    scopes: list[str] = []
    if spec.private_only:
        scopes.append("仅私聊")
    if spec.group_only:
        scopes.append("仅群聊")
    if spec.require_tome:
        scopes.append("群聊需 @机器人")
    return "；".join(scopes)


def _format_command_line(spec: CommandSpec) -> str:
    usage = spec.usage or spec.name
    description = f"：{spec.description}" if spec.description else ""
    scope = _command_scope(spec)
    scope_text = f"（{scope}）" if scope else ""
    return f"- {usage}{description}{scope_text}"


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
        return "命令：\n- 暂无已注册命令"
    return "\n".join(["命令：", *[_format_command_line(spec) for spec in commands]])


def _format_command_detail(spec: CommandSpec) -> str:
    lines = [
        f"命令：{spec.name}",
        f"用法：{spec.usage or spec.name}",
    ]
    if spec.description:
        lines.append(f"说明：{spec.description}")
    if spec.aliases:
        lines.append(f"别名：{', '.join(spec.aliases)}")
    scope = _command_scope(spec)
    if scope:
        lines.append(f"限制：{scope}")
    return "\n".join(lines)


def _summary_help() -> str:
    blocks = [
        ["HIKARI BOT 帮助"],
        _format_command_list().splitlines(),
        FALLBACK_HELP,
        WEB_HELP,
        ["发送「帮助 全部」查看自动解析和更多说明"],
    ]
    return "\n\n".join("\n".join(block) for block in blocks if block)


def _full_help() -> str:
    blocks = [
        ["HIKARI BOT 完整帮助"],
        _format_command_list().splitlines(),
        AUTO_PARSE_HELP,
        FALLBACK_HELP,
        WEB_HELP,
        USAGE_HELP,
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
        await ctx.send(Message(f"没有找到命令：{arg}\n发送「帮助 命令」查看命令列表。"))
        return

    await ctx.send(Message(_format_command_detail(spec)))
