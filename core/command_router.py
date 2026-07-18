"""
轻量命令路由。

明确命令放在这里注册；URL 自动解析和贴纸关键词触发仍然走各自的 fallback。
"""

from __future__ import annotations

import inspect
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Iterable

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent

from core.lifecycle_logging import describe_event, elapsed_ms, preview_text

logger = logging.getLogger("HikariBot.CommandRouter")


@dataclass(slots=True)
class CommandContext:
    bot: Bot
    event: MessageEvent
    text: str
    args: str
    command: str
    matched: str

    async def send(self, message) -> None:
        await self.bot.send(self.event, message)


CommandHandler = Callable[[CommandContext], Awaitable[None] | None]


@dataclass(slots=True)
class CommandSpec:
    name: str
    aliases: tuple[str, ...]
    handler: CommandHandler
    description: str = ""
    usage: str = ""
    detail_key: str = ""
    require_tome: bool = False
    private_only: bool = False
    group_only: bool = False
    show_in_help: bool = True

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


_commands: list[CommandSpec] = []
_handled_event_keys: dict[str, float] = {}
_HANDLED_TTL_SECONDS = 300


def _event_key(event: MessageEvent) -> str:
    """为事件生成全局唯一键，避免 id() 内存地址重用导致误判。

    Python 的 id() 返回对象内存地址，GC 后可被新对象重用。
    用 (会话ID + 消息ID) 替代，这两个字段在 OneBot 中是消息级别的全局唯一标识。
    """
    return f"{event.get_session_id()}:{event.message_id}"


def command(
    name: str,
    *,
    aliases: Iterable[str] = (),
    description: str = "",
    usage: str = "",
    detail_key: str = "",
    require_tome: bool = False,
    private_only: bool = False,
    group_only: bool = False,
    show_in_help: bool = True,
) -> Callable[[CommandHandler], CommandHandler]:
    """注册一个明确命令。"""

    def decorator(func: CommandHandler) -> CommandHandler:
        spec = CommandSpec(
            name=name,
            aliases=tuple(aliases),
            handler=func,
            description=description,
            usage=usage or name,
            detail_key=detail_key,
            require_tome=require_tome,
            private_only=private_only,
            group_only=group_only,
            show_in_help=show_in_help,
        )
        _commands.append(spec)
        logger.info(
            "已注册命令: %s aliases=%s scope=%s",
            name,
            ",".join(spec.aliases) if spec.aliases else "-",
            _scope_label(spec),
        )
        return func

    return decorator


def iter_commands() -> list[CommandSpec]:
    return list(_commands)


def format_command_help() -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for spec in _commands:
        if not spec.show_in_help:
            continue
        if spec.name in seen:
            continue
        seen.add(spec.name)
        description = f"：{spec.description}" if spec.description else ""
        lines.append(f"- {spec.name}{description}")
    return "\n".join(lines)


def is_command_handled(event: MessageEvent) -> bool:
    _cleanup_handled_events()
    return _event_key(event) in _handled_event_keys


def mark_event_handled(event: MessageEvent) -> None:
    _mark_command_handled(event)


def _mark_command_handled(event: MessageEvent) -> None:
    _cleanup_handled_events()
    _handled_event_keys[_event_key(event)] = time.monotonic()


def _cleanup_handled_events() -> None:
    if len(_handled_event_keys) < 1000:
        return
    now = time.monotonic()
    expired = [
        key
        for key, marked_at in _handled_event_keys.items()
        if now - marked_at > _HANDLED_TTL_SECONDS
    ]
    for key in expired:
        _handled_event_keys.pop(key, None)


def _normalize_for_match(value: str) -> str:
    return value.strip().casefold()


def _match_command(text: str) -> tuple[CommandSpec, str, str] | None:
    stripped = text.strip()
    if not stripped:
        return None

    candidates: list[tuple[int, CommandSpec, str, str]] = []
    folded_text = stripped.casefold()

    for spec in _commands:
        for name in spec.names:
            normalized_name = _normalize_for_match(name)
            if not normalized_name:
                continue

            if folded_text == normalized_name:
                candidates.append((len(name), spec, name, ""))
                continue

            prefix = f"{normalized_name} "
            if folded_text.startswith(prefix):
                args = stripped[len(name):].strip()
                candidates.append((len(name), spec, name, args))

    if not candidates:
        return None

    _, spec, matched, args = max(candidates, key=lambda item: item[0])
    return spec, matched, args


def _scope_allowed(spec: CommandSpec, event: MessageEvent) -> bool:
    is_group = isinstance(event, GroupMessageEvent)
    if spec.private_only and is_group:
        return False
    if spec.group_only and not is_group:
        return False
    if spec.require_tome and is_group and not event.is_tome():
        return False
    return True


command_matcher = on_message(priority=0, block=False)


@command_matcher.handle()
async def _handle_command(bot: Bot, event: MessageEvent) -> None:
    text = event.get_plaintext().strip()
    matched = _match_command(text)
    if matched is None:
        return

    spec, matched_name, args = matched
    if not _scope_allowed(spec, event):
        logger.info(
            "[Command] 作用域拦截 command=%s matched=%r %s",
            spec.name,
            matched_name,
            describe_event(event, text),
        )
        return

    ctx = CommandContext(
        bot=bot,
        event=event,
        text=text,
        args=args,
        command=spec.name,
        matched=matched_name,
    )

    logger.info(
        "[Command] 命中 command=%s matched=%r args_len=%d args_preview=%r %s",
        spec.name,
        matched_name,
        len(args),
        preview_text(args),
        describe_event(event, text),
    )
    _mark_command_handled(event)
    started_at = time.monotonic()
    try:
        result = spec.handler(ctx)
        if inspect.isawaitable(result):
            await result
    except Exception as e:
        logger.exception(
            "[Command] 命令 %s 处理异常 elapsed=%.1fms: %s",
            spec.name,
            elapsed_ms(started_at),
            e,
        )
        try:
            from core.error_notifier import notify_error_to_superuser, send_user_error

            await send_user_error(bot, event)
            await notify_error_to_superuser(bot, event, e, f"Command:{spec.name}")
        except Exception as notify_err:
            logger.exception("[Command] 发送错误通知失败: %s", notify_err)
    else:
        logger.info(
            "[Command] 完成 command=%s elapsed=%.1fms %s",
            spec.name,
            elapsed_ms(started_at),
            describe_event(event),
        )


def _scope_label(spec: CommandSpec) -> str:
    scopes: list[str] = []
    if spec.private_only:
        scopes.append("private_only")
    if spec.group_only:
        scopes.append("group_only")
    if spec.require_tome:
        scopes.append("require_tome")
    return ",".join(scopes) if scopes else "any"
