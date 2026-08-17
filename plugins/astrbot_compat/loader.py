"""AstrBot plugin loader — dynamic import, handler bridge, command registration.

This is the core module that bridges AstrBot plugin API calls to the
HIKARI BOT NEO runtime.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import logging
import re
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent

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
from astrbot.core.message.message_event_result import MessageEventResult

from core.command_router import _commands
from plugins.astrbot_compat.state import (
    OnMsgHandler,
    PluginHandle,
    RegexMatcher,
    loaded_plugins as _loaded_plugins,
    on_message_handlers as _on_message_handlers,
    regex_matchers as _regex_matchers,
)

logger = logging.getLogger("AstrBotCompat.Loader")

from plugins.astrbot_compat.dispatch import dispatch_regex_command, dispatch_on_message

# ---------------------------------------------------------------------------
# Core loading logic
# ---------------------------------------------------------------------------

async def load_plugin(
    plugin_dir: Path,
    plugin_name: str | None = None,
    shim_path: Path | None = None,
) -> PluginHandle:
    """Load an astrbot plugin from its directory.

    Steps:
        1. Add the shared shim to ``sys.path``
        2. Import ``main.py`` as a unique package
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
    _add_to_sys_path(shim_path)

    # --- Install dependencies if needed ---
    requirements_txt = plugin_dir / "requirements.txt"
    deps_installed = []
    if requirements_txt.exists():
        deps_installed = _install_requirements(requirements_txt, plugin_name)
        if deps_installed:
            logger.info("Plugin [%s] deps installed: %s", plugin_name, deps_installed)

    # --- Import main module as an isolated package ---
    module_prefix = _module_prefix(plugin_name, plugin_dir)
    _clear_plugin_modules(module_prefix)
    _clear_star_registrations(module_prefix)
    spec = importlib.util.spec_from_file_location(
        module_prefix,
        main_py,
        submodule_search_locations=[str(plugin_dir.resolve())],
    )
    if spec is None or spec.loader is None:
        raise ValueError(f"Failed to create import spec for plugin {plugin_name}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_prefix] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        _clear_star_registrations(module_prefix)
        _clear_plugin_modules(module_prefix)
        raise ValueError(f"Failed to import plugin {plugin_name}: {e}") from e

    # --- Find Star subclass ---
    star_classes_after = get_registered_star_classes()
    star_cls = star_classes_after.get(module_prefix)
    if star_cls is None:
        logger.debug(
            "Plugin [%s] no Star via __init_subclass__, scanning module ...",
            plugin_name,
        )
        found = _find_star_in_module(mod)
        if found:
            star_cls = found

    if star_cls is None:
        _clear_star_registrations(module_prefix)
        _clear_plugin_modules(module_prefix)
        raise ValueError(
            f"No Star subclass found in plugin {plugin_name}. "
            "Make sure the plugin class inherits from astrbot.api.star.Star"
        )

    logger.debug("Plugin [%s] Star class: %s (module=%s)", plugin_name, star_cls.__name__, module_prefix)

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
    except TypeError:
        # Some plugins don't accept config
        try:
            instance = star_cls(context=ctx)
            logger.debug("Plugin [%s] instantiated without config (fallback)", plugin_name)
        except TypeError as e2:
            _clear_star_registrations(module_prefix)
            _clear_plugin_modules(module_prefix)
            raise ValueError(
                f"Failed to instantiate plugin {plugin_name}: {e2}"
            ) from e2
    except Exception:
        _clear_star_registrations(module_prefix)
        _clear_plugin_modules(module_prefix)
        raise

    # --- Register handlers ---
    handle = PluginHandle(
        name=plugin_name,
        display_name=getattr(star_cls, "name", "") or star_cls.__name__,
        module_path=plugin_dir,
        module_prefix=module_prefix,
        module=mod,
        star_class=star_cls,
        instance=instance,
        ctx=ctx,
        config_obj=config_obj,
    )

    # --- Set bot ref for Context.send_message ---
    _try_set_bot_ref()

    # --- Initialize before exposing any handlers ---
    try:
        await instance.initialize()
        logger.debug("Plugin [%s] initialize() completed", plugin_name)
    except Exception as e:
        logger.exception("Plugin [%s] initialize() failed: %s", plugin_name, e)
        try:
            await instance.terminate()
        except Exception:
            logger.exception("Plugin [%s] rollback terminate() failed", plugin_name)
        _rollback_registrations(handle)
        _clear_star_registrations(module_prefix)
        _clear_plugin_modules(module_prefix)
        raise ValueError(f"Plugin {plugin_name} initialize() failed: {e}") from e

    try:
        _register_handlers(handle)
    except Exception:
        try:
            await instance.terminate()
        except Exception:
            logger.exception("Plugin [%s] registration rollback terminate() failed", plugin_name)
        _rollback_registrations(handle)
        _clear_star_registrations(module_prefix)
        _clear_plugin_modules(module_prefix)
        raise

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


async def unload_plugin(name: str) -> None:
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
        await handle.instance.terminate()
        logger.debug("Plugin [%s] terminate() completed", name)
    except Exception as e:
        logger.warning("Plugin [%s] terminate() raised: %s", name, e)

    # Remove commands from command_router
    removed_count, regex_removed, on_msg_removed = _rollback_registrations(handle)

    # Clean shim star registration
    _clear_star_registrations(handle.module_prefix)
    mod_names = _clear_plugin_modules(handle.module_prefix)

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


async def reload_plugin(name: str, shim_path: Path | None = None) -> PluginHandle:
    """Reload a plugin: unload then load again."""
    plugin_dir: Path | None = None
    if name in _loaded_plugins:
        plugin_dir = _loaded_plugins[name].module_path
        logger.info("Reloading plugin [%s] ...", name)
        await unload_plugin(name)

    if plugin_dir is None:
        raise ValueError(f"Cannot reload plugin that was never loaded: {name}")

    return await load_plugin(plugin_dir, plugin_name=name, shim_path=shim_path)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_shim_path(shim_path: Path | None) -> Path:
    if shim_path is not None:
        return shim_path
    return Path(__file__).resolve().parent / "shim"


def _add_to_sys_path(p: Path | str) -> None:
    s = str(Path(p).resolve())
    if s not in sys.path:
        sys.path.insert(0, s)


def _remove_from_sys_path(s: str) -> None:
    while s in sys.path:
        sys.path.remove(s)


def _module_prefix(plugin_name: str, plugin_dir: Path) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9_]", "_", plugin_name).strip("_") or "plugin"
    if safe_name[0].isdigit():
        safe_name = f"p_{safe_name}"
    path_hash = hashlib.sha256(str(plugin_dir.resolve()).encode("utf-8")).hexdigest()[:12]
    return f"_astrbot_plugin_{safe_name}_{path_hash}"


def _clear_plugin_modules(module_prefix: str) -> list[str]:
    module_names = [
        name
        for name in list(sys.modules)
        if name == module_prefix or name.startswith(f"{module_prefix}.")
    ]
    for name in module_names:
        sys.modules.pop(name, None)
    return module_names


def _clear_star_registrations(module_prefix: str) -> None:
    for module_name in get_registered_star_classes():
        if module_name == module_prefix or module_name.startswith(f"{module_prefix}."):
            clear_star_registration(module_name)


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
            matcher = RegexMatcher(handle.name, regex_pat, method)
            _regex_matchers.append(matcher)
            handle.regex_handlers.append(matcher)
            logger.debug(
                "Plugin [%s] registered regex: %s",
                handle.name,
                regex_pat.pattern,
            )
            continue

        # --- @filter.on_message ---
        if is_on_message(method):
            handler = OnMsgHandler(handle.name, method)
            _on_message_handlers.append(handler)
            handle.on_message_handlers.append(handler)
            logger.debug(
                "Plugin [%s] registered on_message handler: %s",
                handle.name,
                method.__name__,
            )
            continue

    _ensure_astrbot_matcher()


def _rollback_registrations(handle: PluginHandle) -> tuple[int, int, int]:
    command_ids = {id(spec) for spec in handle.command_specs}
    commands_before = len(_commands)
    _commands[:] = [spec for spec in _commands if id(spec) not in command_ids]
    regex_ids = {id(item) for item in handle.regex_handlers}
    on_message_ids = {id(item) for item in handle.on_message_handlers}
    regex_removed = sum(1 for item in _regex_matchers if id(item) in regex_ids)
    on_msg_removed = sum(1 for item in _on_message_handlers if id(item) in on_message_ids)
    _regex_matchers[:] = [item for item in _regex_matchers if id(item) not in regex_ids]
    _on_message_handlers[:] = [item for item in _on_message_handlers if id(item) not in on_message_ids]
    handle.command_specs.clear()
    handle.regex_handlers.clear()
    handle.on_message_handlers.clear()
    handle.command_names.clear()
    handle._command_aliases.clear()
    return commands_before - len(_commands), regex_removed, on_msg_removed


def _register_one_command(
    handle: PluginHandle,
    method: Any,
    cmd_meta: dict[str, Any],
) -> None:
    """Register a single command handler into ``command_router``."""
    from core.command_router import command as register_command

    cmd_name = cmd_meta["name"]
    # Add /cmd_name as an alias so /command works (command_router doesn't strip /)
    alias_list = list(cmd_meta["alias"])
    if f"/{cmd_name}" not in alias_list:
        alias_list.append(f"/{cmd_name}")
    param_info = cmd_meta.get("params", [])
    perm = cmd_meta.get("permission", "all")
    evt_type = cmd_meta.get("event_type", "all")

    instance = handle.instance

    async def _wrapped_handler(ctx: Any) -> None:
        event = ctx.event
        text = ctx.text
        bot = ctx.bot

        if not await _permission_allowed(perm, bot, event):
            return

        # Strip leading / from text for matching
        clean_text = text.lstrip("/") if text.startswith("/") else text

        # Parse arguments if param_info is available
        if param_info:
            cmd_prefix = ctx.command if ctx.command else (cmd_name + " ")
            args_str = clean_text
            if args_str.lower().startswith(cmd_prefix.lower()):
                args_str = args_str[len(cmd_prefix):].strip()
            parsed = parse_command_args(args_str, param_info)
        else:
            parsed = {}

        from plugins.astrbot_compat.dispatch import _run_generator

        astr_event = _make_astr_event(bot, event, clean_text)

        if parsed:
            await _run_generator(instance, method, astr_event, bot, event, **parsed)
        else:
            await _run_generator(instance, method, astr_event, bot, event)

    # Permission is checked against the real sender inside the wrapped handler.
    scopes: dict[str, Any] = {}
    if evt_type == "group":
        scopes["group_only"] = True
    elif evt_type == "private":
        scopes["private_only"] = True

    commands_before = len(_commands)
    register_command(
        cmd_name,
        aliases=alias_list,
        description=f"[AstrBot] {cmd_name}",
        **scopes,
    )(_wrapped_handler)

    if len(_commands) != commands_before + 1:
        raise RuntimeError(f"Command registration failed for {cmd_name}")
    handle.command_specs.append(_commands[-1])

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


async def _permission_allowed(perm: str, bot: Bot, event: MessageEvent) -> bool:
    if perm not in ("admin", "superuser"):
        return True

    from core.command_router import is_superuser_event

    if is_superuser_event(event):
        return True
    if perm == "superuser" or not isinstance(event, GroupMessageEvent):
        return False

    role = str(getattr(getattr(event, "sender", None), "role", "") or "").casefold()
    if role in ("owner", "admin"):
        return True
    if role:
        return False

    try:
        member = await bot.get_group_member_info(
            group_id=event.group_id,
            user_id=event.user_id,
            no_cache=True,
        )
    except Exception as exc:
        logger.warning("Failed to verify AstrBot group permission: %s", exc)
        return False
    return str(member.get("role", "")).casefold() in ("owner", "admin")


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
