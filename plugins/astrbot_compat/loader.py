"""AstrBot plugin loader — dynamic import, handler bridge, command registration.

This is the core module that bridges AstrBot plugin API calls to the
HIKARI BOT NEO runtime.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import re
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any, AsyncGenerator

from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, MessageSegment

from astrbot.api.star import Context, Star, clear_star_registration, get_registered_star_classes
from astrbot.api.AstrBotConfig import AstrBotConfig
from astrbot.api.event import AstrMessageEvent
from astrbot.api.event.filter import (
    get_command_meta,
    get_regex_meta,
    is_on_message,
    get_permission_meta,
    get_event_type_meta,
    parse_command_args,
)
from astrbot.api.message_components import (
    BaseMessageComponent,
    Image,
    Json as JsonComp,
    Node as NodeComp,
    Plain,
    Record,
    Reply as ReplyComp,
    Share as ShareComp,
    Video,
)
from astrbot.core.message.message_event_result import MessageChain, MessageEventResult

from core.command_router import CommandSpec, _commands
from core.lifecycle_logging import describe_event

logger = logging.getLogger("AstrBotCompat.Loader")

# ---------------------------------------------------------------------------
# Public state: loaded plugins tracked by the manager
# ---------------------------------------------------------------------------

_loaded_plugins: dict[str, "PluginHandle"] = {}
_regex_matchers: list["RegexMatcher"] = []
_on_message_handlers: list["OnMsgHandler"] = []


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class PluginHandle:
    """Tracks a loaded astrbot plugin's runtime state."""

    def __init__(
        self,
        name: str,
        display_name: str,
        module_path: Path,
        module: ModuleType,
        star_class: type[Star],
        instance: Star,
        ctx: Context,
        config_obj: AstrBotConfig,
    ):
        self.name = name
        self.display_name = display_name
        self.module_path = module_path
        self.module = module
        self.star_class = star_class
        self.instance = instance
        self.ctx = ctx
        self.config_obj = config_obj
        self.command_names: list[str] = []  # primary command names registered in command_router
        self._command_aliases: dict[str, list[str]] = {}  # primary name -> aliases
        self._load_timestamp: float = time.monotonic()

    @property
    def info(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "class": self.star_class.__name__,
            "author": getattr(self.star_class, "author", ""),
            "version": getattr(self.star_class, "version", ""),
            "commands": list(self.command_names),
            "path": str(self.module_path),
        }


class RegexMatcher:
    """A loaded plugin's regex handler."""

    def __init__(self, plugin_name: str, pattern: re.Pattern, handler: Any):
        self.plugin_name = plugin_name
        self.pattern = pattern
        self.handler = handler


class OnMsgHandler:
    """A loaded plugin's catch-all message handler."""

    def __init__(self, plugin_name: str, handler: Any):
        self.plugin_name = plugin_name
        self.handler = handler


# ---------------------------------------------------------------------------
# Core loading logic
# ---------------------------------------------------------------------------

def load_plugin(
    plugin_dir: Path,
    plugin_name: str | None = None,
    shim_path: Path | None = None,
) -> PluginHandle:
    """Load an astrbot plugin from its directory.

    Steps:
        1. Add shim & plugin dir to ``sys.path``
        2. Import ``main`` module
        3. Find the ``Star`` subclass
        4. Parse config, instantiate, register handlers
        5. Call ``initialize()``

    Returns:
        A ``PluginHandle`` that tracks the loaded plugin.

    Raises:
        ValueError: If ``main.py`` is missing or no Star subclass found.
    """
    from plugins.astrbot_compat.config import build_config_path, parse_metadata, parse_schema

    started_at = time.monotonic()
    logger.info("Loading plugin [%s] from %s ...", plugin_name or "?", plugin_dir)

    # --- Validate structure ---
    main_py = plugin_dir / "main.py"
    if not main_py.exists():
        raise ValueError(f"Plugin has no main.py: {plugin_dir}")

    # --- Resolve name ---
    if plugin_name is None:
        plugin_name = plugin_dir.name

    # --- Read metadata ---
    metadata = parse_metadata(plugin_dir)

    # --- Prepare paths ---
    shim_path = _resolve_shim_path(shim_path)
    plugin_source = str(plugin_dir.resolve())

    _add_to_sys_path(shim_path)
    _add_to_sys_path(plugin_source)

    # --- Install dependencies if needed ---
    requirements_txt = plugin_dir / "requirements.txt"
    deps_installed = []
    if requirements_txt.exists():
        deps_installed = _install_requirements(requirements_txt, plugin_name)
        if deps_installed:
            logger.info("Plugin [%s] deps installed: %s", plugin_name, deps_installed)

    # --- Import main module ---
    star_classes_before = set(get_registered_star_classes().keys())
    try:
        mod = importlib.import_module("main")
    except ImportError as e:
        _remove_from_sys_path(plugin_source)
        raise ValueError(f"Failed to import plugin {plugin_name}: {e}") from e

    # --- Find Star subclass ---
    star_classes_after = get_registered_star_classes()
    new_modules = set(star_classes_after.keys()) - star_classes_before
    if not new_modules:
        # The plugin might have been imported with a different module path
        # Try scanning all star classes
        for mod_name, cls in star_classes_after.items():
            if mod_name.startswith("main") or mod_name == mod.__name__:
                new_modules.add(mod_name)

    if not new_modules:
        logger.debug(
            "Plugin [%s] no Star via __init_subclass__, scanning module ...",
            plugin_name,
        )
        found = _find_star_in_module(mod)
        if found:
            star_classes_after[mod.__name__] = found
            new_modules.add(mod.__name__)

    if not new_modules:
        raise ValueError(
            f"No Star subclass found in plugin {plugin_name}. "
            "Make sure the plugin class inherits from astrbot.api.star.Star"
        )

    cls_module_name = next(iter(new_modules))
    star_cls = star_classes_after[cls_module_name]
    logger.debug("Plugin [%s] Star class: %s (module=%s)", plugin_name, star_cls.__name__, cls_module_name)

    # --- Apply metadata to Star class ---
    if metadata.get("name"):
        star_cls.name = metadata["name"]
    if metadata.get("version"):
        star_cls.version = metadata["version"]
    if metadata.get("author"):
        star_cls.author = metadata["author"]

    # --- Parse config ---
    schema_info = parse_schema(plugin_dir / "_conf_schema.json")
    config_path = build_config_path(plugin_name)
    config_obj = AstrBotConfig(
        config_path=config_path,
        initial=schema_info["defaults"],
    )
    logger.debug(
        "Plugin [%s] config loaded: %s (%d keys)",
        plugin_name,
        config_path,
        len(config_obj),
    )

    # --- Create Context ---
    ctx = Context(plugin_name=plugin_name, config=config_obj)

    # --- Instantiate ---
    try:
        instance = star_cls(context=ctx, config=config_obj)
    except TypeError as e:
        # Some plugins don't accept config
        try:
            instance = star_cls(context=ctx)
            logger.debug("Plugin [%s] instantiated without config (fallback)", plugin_name)
        except TypeError as e2:
            raise ValueError(
                f"Failed to instantiate plugin {plugin_name}: {e2}"
            ) from e2

    # --- Register handlers ---
    handle = PluginHandle(
        name=plugin_name,
        display_name=getattr(star_cls, "name", "") or star_cls.__name__,
        module_path=plugin_dir,
        module=mod,
        star_class=star_cls,
        instance=instance,
        ctx=ctx,
        config_obj=config_obj,
    )

    _register_handlers(handle)

    # --- Set bot ref for Context.send_message ---
    _try_set_bot_ref()

    # --- Call initialize ---
    try:
        import asyncio
        asyncio.get_event_loop().run_until_complete(instance.initialize())
        logger.debug("Plugin [%s] initialize() completed", plugin_name)
    except Exception as e:
        logger.warning(
            "Plugin [%s] initialize() raised an error (plugin may be partially loaded): %s",
            plugin_name,
            e,
        )

    elapsed = time.monotonic() - started_at
    cmd_count = len(handle.command_names)
    regex_count = sum(1 for r in _regex_matchers if r.plugin_name == plugin_name)
    on_msg_count = sum(1 for o in _on_message_handlers if o.plugin_name == plugin_name)

    logger.info(
        "Plugin [%s] — loaded in %.2fs "
        "class=%s commands=%d regex=%d on_message=%d config_keys=%d deps_installed=%s",
        plugin_name,
        elapsed,
        star_cls.__name__,
        cmd_count,
        regex_count,
        on_msg_count,
        len(config_obj),
        bool(deps_installed),
    )

    return handle


def unload_plugin(name: str) -> None:
    """Unload a previously loaded plugin.

    Removes its commands from ``command_router._commands``, regex/on_message
    handlers, and calls ``terminate()``.
    """
    handle = _loaded_plugins.get(name)
    if handle is None:
        raise ValueError(f"Plugin not loaded: {name}")

    started_at = time.monotonic()
    logger.info("Unloading plugin [%s] ...", name)

    # Call terminate
    try:
        import asyncio
        asyncio.get_event_loop().run_until_complete(handle.instance.terminate())
        logger.debug("Plugin [%s] terminate() completed", name)
    except Exception as e:
        logger.warning("Plugin [%s] terminate() raised: %s", name, e)

    # Remove commands from command_router
    removed_count = 0
    for cmd_name in handle.command_names:
        spec_count_before = len(_commands)
        _commands[:] = [spec for spec in _commands if spec.name != cmd_name]
        removed_count += spec_count_before - len(_commands)

    # Remove regex matchers
    regex_removed = len([r for r in _regex_matchers if r.plugin_name == name])
    _regex_matchers[:] = [r for r in _regex_matchers if r.plugin_name != name]

    # Remove on_message handlers
    on_msg_removed = len([o for o in _on_message_handlers if o.plugin_name == name])
    _on_message_handlers[:] = [o for o in _on_message_handlers if o.plugin_name != name]

    # Clean shim star registration
    clear_star_registration(handle.module.__name__)

    # Remove from sys.path
    _remove_from_sys_path(str(handle.module_path.resolve()))

    # Remove from sys.modules
    mod_names = [
        m for m in sys.modules
        if m == handle.module.__name__ or m.startswith(f"{handle.module.__name__}.")
    ]
    for m in mod_names:
        sys.modules.pop(m, None)

    # Remove from loaded dict
    _loaded_plugins.pop(name, None)

    elapsed = time.monotonic() - started_at
    logger.info(
        "Plugin [%s] — unloaded in %.2fs "
        "commands_removed=%d regex_removed=%d on_message_removed=%d modules_cleaned=%d",
        name,
        elapsed,
        removed_count,
        regex_removed,
        on_msg_removed,
        len(mod_names),
    )


def reload_plugin(name: str, shim_path: Path | None = None) -> PluginHandle:
    """Reload a plugin: unload then load again."""
    plugin_dir: Path | None = None
    if name in _loaded_plugins:
        plugin_dir = _loaded_plugins[name].module_path
        logger.info("Reloading plugin [%s] ...", name)
        unload_plugin(name)

    if plugin_dir is None:
        raise ValueError(f"Cannot reload plugin that was never loaded: {name}")

    return load_plugin(plugin_dir, plugin_name=name, shim_path=shim_path)


# ---------------------------------------------------------------------------
# Handler bridge — dispatched from the NoneBot matcher
# ---------------------------------------------------------------------------

async def dispatch_regex_command(
    bot: Bot,
    event: MessageEvent,
    text: str,
) -> bool:
    """Dispatch a message to all loaded regex handlers. Return True if matched."""
    matched = False
    for regex_matcher in _regex_matchers:
        m = regex_matcher.pattern.search(text)
        if m:
            plugin = _loaded_plugins.get(regex_matcher.plugin_name)
            if plugin is None:
                logger.debug("Regex match but plugin %s is gone", regex_matcher.plugin_name)
                continue
            matched = True
            logger.debug(
                "Regex matched: plugin=[%s] pattern=%s text=%r groups=%s",
                regex_matcher.plugin_name,
                regex_matcher.pattern.pattern,
                text[:80],
                m.groupdict(),
            )
            await _run_handler(
                plugin,
                regex_matcher.handler,
                bot,
                event,
                text,
                **m.groupdict(),
            )
    return matched


async def dispatch_on_message(
    bot: Bot,
    event: MessageEvent,
    text: str,
) -> bool:
    """Dispatch a message to all loaded catch-all handlers. Return True if any handled."""
    handled = False
    for on_msg in _on_message_handlers:
        plugin = _loaded_plugins.get(on_msg.plugin_name)
        if plugin is None:
            continue
        handled = True
        logger.debug(
            "on_message dispatch: plugin=[%s] text=%r",
            on_msg.plugin_name,
            text[:80],
        )
        await _run_handler(plugin, on_msg.handler, bot, event, text)
    return handled


# ---------------------------------------------------------------------------
# Conversion utilities
# ---------------------------------------------------------------------------

def convert_chain_to_onebot(chain: MessageChain) -> str | list[MessageSegment]:
    """Convert a ``MessageChain`` to a OneBot-compatible message object."""
    segments: list[MessageSegment] = []

    for comp in chain.chain:
        seg = _component_to_segment(comp)
        if seg is not None:
            segments.append(seg)

    if not segments:
        return ""

    if len(segments) == 1 and segments[0].type == "text":
        return segments[0].data.get("text", "")

    return segments


def _component_to_segment(comp: BaseMessageComponent) -> MessageSegment | None:
    if isinstance(comp, Plain):
        return MessageSegment.text(comp.text)
    if isinstance(comp, Image):
        if comp.url:
            return MessageSegment.image(comp.url)
        if comp.file:
            return MessageSegment.image(comp.file)
        if comp.path:
            return MessageSegment.image(comp.path)
        return None
    if isinstance(comp, Record):
        url = comp.url or comp.file or comp.path
        if url:
            return MessageSegment.record(url)
        return None
    if isinstance(comp, Video):
        url = comp.url or comp.file
        if url:
            return MessageSegment.video(url)
        return None
    if isinstance(comp, ReplyComp):
        # Reply content can't be directly sent as a standalone segment;
        # fall back to text description
        text = f"[回复 {comp.id}]"
        if comp.message_str:
            text += f" {comp.message_str}"
        elif comp.sender_nickname:
            text += f" ({comp.sender_nickname})"
        return MessageSegment.text(text)
    if isinstance(comp, ShareComp):
        text = f"🔗 {comp.title}: {comp.url}"
        return MessageSegment.text(text)
    if isinstance(comp, JsonComp) and comp.data:
        import json
        try:
            return MessageSegment.json(json.dumps(comp.data, ensure_ascii=False))
        except (TypeError, ValueError):
            return MessageSegment.text(str(comp.data))
    if isinstance(comp, NodeComp):
        # Node forwarding is too complex for v1 shim; fall back to text
        texts = []
        for child in (comp.content or []):
            seg = _component_to_segment(child)
            if seg and seg.type == "text":
                texts.append(seg.data.get("text", ""))
        return MessageSegment.text("[转发消息] " + " | ".join(texts)) if texts else None
    if hasattr(comp, "text") and comp.text:
        return MessageSegment.text(str(comp.text))
    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_shim_path(shim_path: Path | None) -> Path:
    if shim_path is not None:
        return shim_path
    return Path(__file__).resolve().parent / "shim"


def _add_to_sys_path(p: Path) -> None:
    s = str(p.resolve())
    if s not in sys.path:
        sys.path.insert(0, s)


def _remove_from_sys_path(s: str) -> None:
    while s in sys.path:
        sys.path.remove(s)


def _install_requirements(requirements_txt: Path, plugin_name: str) -> list[str]:
    """Install requirements into the shared plugin venv."""
    from plugins.astrbot_compat.venv_manager import PluginVenvManager
    from plugins.astrbot_compat.constants import PLUGINS_DIR

    venv_mgr = PluginVenvManager(PLUGINS_DIR / ".venv")
    deps = venv_mgr.parse_requirements(requirements_txt)
    if not deps:
        return []
    try:
        venv_mgr.install_deps(deps)
        venv_mgr.add_to_path()
        logger.debug("Plugin [%s] deps added to sys.path from shared venv", plugin_name)
        return deps
    except RuntimeError as e:
        logger.error(
            "Plugin [%s] dependency installation failed (plugin may still work): %s",
            plugin_name,
            e,
        )
        return []


def _try_set_bot_ref() -> None:
    """Try to get the bot instance and store it on Context."""
    try:
        from nonebot import get_bot
        bot = get_bot()
        from astrbot.api.star import _set_bot_ref
        _set_bot_ref(bot)
    except (ValueError, LookupError):
        pass  # Bot not ready yet


def _find_star_in_module(mod: ModuleType) -> type[Star] | None:
    for name in dir(mod):
        obj = getattr(mod, name)
        if isinstance(obj, type) and issubclass(obj, Star) and obj is not Star:
            return obj
    return None


def _register_handlers(handle: PluginHandle) -> None:
    """Scan a plugin class and register all handlers."""
    for attr_name in dir(handle.star_class):
        method = getattr(handle.star_class, attr_name)
        if not inspect.iscoroutinefunction(method) and not inspect.isasyncgenfunction(method):
            continue

        if attr_name.startswith("_"):
            continue

        # Resolve permission & event_type filters
        perm = get_permission_meta(method) or "all"
        evt_type = get_event_type_meta(method) or "all"

        # --- @filter.command ---
        cmd_meta = get_command_meta(method)
        if cmd_meta is not None:
            cmd_meta["permission"] = perm
            cmd_meta["event_type"] = evt_type
            _register_one_command(handle, method, cmd_meta)
            continue

        # --- @filter.regex ---
        regex_pat = get_regex_meta(method)
        if regex_pat is not None:
            _regex_matchers.append(RegexMatcher(handle.name, regex_pat, method))
            logger.debug(
                "Plugin [%s] registered regex: %s",
                handle.name,
                regex_pat.pattern,
            )
            continue

        # --- @filter.on_message ---
        if is_on_message(method):
            _on_message_handlers.append(OnMsgHandler(handle.name, method))
            logger.debug(
                "Plugin [%s] registered on_message handler: %s",
                handle.name,
                method.__name__,
            )
            continue

    _ensure_astrbot_matcher()


def _register_one_command(
    handle: PluginHandle,
    method: Any,
    cmd_meta: dict[str, Any],
) -> None:
    """Register a single command handler into ``command_router``."""
    from core.command_router import command as register_command

    cmd_name = cmd_meta["name"]
    alias_list = list(cmd_meta["alias"])
    param_info = cmd_meta.get("params", [])
    perm = cmd_meta.get("permission", "all")
    evt_type = cmd_meta.get("event_type", "all")

    instance = handle.instance

    async def _wrapped_handler(ctx: Any) -> None:
        event = ctx.event
        text = ctx.text
        bot = ctx.bot

        # Parse arguments if param_info is available
        if param_info:
            # Extract raw args (text after command name)
            cmd_prefix = ctx.command if ctx.command else (cmd_name + " ")
            args_str = text
            if args_str.lower().startswith(cmd_prefix.lower()):
                args_str = args_str[len(cmd_prefix):].strip()
            parsed = parse_command_args(args_str, param_info)
        else:
            parsed = {}

        astr_event = _make_astr_event(bot, event, text)

        # Inject parsed args as **kwargs if not already in the method call
        if parsed:
            await _run_generator(instance, method, astr_event, bot, event, **parsed)
        else:
            await _run_generator(instance, method, astr_event, bot, event)

    # Build scope restrictions from permission / event_type
    scopes: dict[str, Any] = {}
    if perm == "admin":
        scopes["require_tome"] = True
    elif perm == "superuser":
        scopes["private_only"] = True
    if evt_type == "group":
        scopes["group_only"] = True
    elif evt_type == "private":
        scopes["private_only"] = True

    register_command(
        cmd_name,
        aliases=alias_list,
        description=f"[AstrBot] {cmd_name}",
        **scopes,
    )(_wrapped_handler)

    handle.command_names.append(cmd_name)
    handle._command_aliases[cmd_name] = alias_list

    alias_str = f" (alias: {alias_list})" if alias_list else ""
    params_str = f" params={len(param_info)}" if param_info else ""
    perm_str = f" perm={perm}" if perm != "all" else ""
    logger.debug(
        "Plugin [%s] registered command: /%s%s%s%s",
        handle.name,
        cmd_name,
        alias_str,
        params_str,
        perm_str,
    )


def _make_astr_event(
    bot: Bot,
    event: MessageEvent,
    text: str,
) -> AstrMessageEvent:
    """Create a shim ``AstrMessageEvent`` from a OneBot event."""
    return AstrMessageEvent(
        message_str=text,
        message_obj=event,
        platform_meta=None,
        session_id=event.get_session_id(),
        bot=bot,
        event=event,
    )


async def _run_handler(
    handle: PluginHandle,
    method: Any,
    bot: Bot,
    event: MessageEvent,
    text: str,
    **extra_kwargs: Any,
) -> None:
    """Run a plugin handler (regex or on_message) bridging yield results."""
    astr_event = _make_astr_event(bot, event, text)
    await _run_generator(handle.instance, method, astr_event, bot, event, **extra_kwargs)


async def _run_generator(
    instance: Any,
    method: Any,
    astr_event: AstrMessageEvent,
    bot: Bot,
    event: MessageEvent,
    **extra_kwargs: Any,
) -> None:
    """Consume an async generator handler and send results."""
    if extra_kwargs:
        gen = method(instance, astr_event, **extra_kwargs)
    else:
        gen = method(instance, astr_event)

    try:
        if inspect.isasyncgen(gen):
            async for result in gen:
                if isinstance(result, MessageEventResult):
                    await _send_result(bot, event, result)
                    if result.is_stopped():
                        break
                elif isinstance(result, str):
                    await bot.send(event, result)
        else:
            # Regular coroutine that may return something
            result = await gen
            if isinstance(result, MessageEventResult):
                await _send_result(bot, event, result)
            elif isinstance(result, str):
                await bot.send(event, result)
    except StopAsyncIteration:
        pass
    except Exception as e:
        logger.exception(
            "Handler error: plugin=[%s] method=%s — %s",
            instance.__class__.__name__ if hasattr(instance, "__class__") else "?",
            method.__name__ if hasattr(method, "__name__") else "?",
            e,
        )


async def _send_result(
    bot: Bot,
    event: MessageEvent,
    result: MessageEventResult,
) -> None:
    """Convert a ``MessageEventResult`` to OneBot messages and send."""
    if not result.chain:
        return

    ob_msg = convert_chain_to_onebot(result)
    if ob_msg:
        await bot.send(event, ob_msg)


# ---------------------------------------------------------------------------
# Lazily-created NoneBot matcher for regex/on_message handlers
# ---------------------------------------------------------------------------

_astrbot_matcher_created = False


def _ensure_astrbot_matcher() -> None:
    """Create a NoneBot matcher at priority 2 to dispatch to regex/on_message handlers."""
    global _astrbot_matcher_created
    if _astrbot_matcher_created:
        return

    from nonebot import on_message

    matcher = on_message(priority=2, block=False)

    @matcher.handle()
    async def _astrbot_compat_handler(bot: Bot, event: MessageEvent) -> None:
        from core.command_router import is_command_handled

        if is_command_handled(event):
            return

        text = event.get_plaintext().strip()

        matched = await dispatch_regex_command(bot, event, text)
        if matched:
            from core.command_router import mark_event_handled
            mark_event_handled(event)
            return

        handled = await dispatch_on_message(bot, event, text)
        if handled:
            from core.command_router import mark_event_handled
            mark_event_handled(event)

    _astrbot_matcher_created = True
    logger.debug("NoneBot matcher created at priority=2 for regex/on_message dispatch")


# ---------------------------------------------------------------------------
# Expose loaded plugins for the manager
# ---------------------------------------------------------------------------

def get_loaded_plugins() -> dict[str, PluginHandle]:
    return _loaded_plugins


def set_loaded_plugin(name: str, handle: PluginHandle) -> None:
    _loaded_plugins[name] = handle
