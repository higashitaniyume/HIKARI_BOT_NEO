"""AstrBot Star & Context shim — plugin base class and interface context."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from astrbot.api.AstrBotConfig import AstrBotConfig

logger = logging.getLogger("AstrBotCompat.Shim.Star")

# Registry: module_path -> class
# Populated by Star.__init_subclass__
_star_classes: dict[str, type] = {}


# ======================================================================
# PluginKVStoreMixin — simple JSON-backed key-value store per plugin
# ======================================================================

class PluginKVStoreMixin:
    """Mixin that provides per-plugin key-value persistence.

    AstrBot plugins inherit this via ``Star``. Data is stored in
    ``UserData/astrbot_plugins/<name>/kv_store.json``.
    """

    _kv_path: Path | None = None

    def set(self, key: str, value: Any) -> None:
        """Store a value."""
        store = self._kv_load()
        store[key] = value
        self._kv_save(store)

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a value."""
        return self._kv_load().get(key, default)

    def delete(self, key: str) -> None:
        """Delete a key."""
        store = self._kv_load()
        store.pop(key, None)
        self._kv_save(store)

    def clear(self) -> None:
        """Clear all stored data."""
        self._kv_save({})

    # Async wrappers used by AstrBot plugins (e.g. get_kv_data/put_kv_data)

    async def get_kv_data(self, key: str, default: Any = None) -> Any:
        """Async version of get(). AstrBot plugins call this for config."""
        return self.get(key, default)

    async def put_kv_data(self, key: str, value: Any) -> None:
        """Async version of set(). AstrBot plugins call this for config."""
        self.set(key, value)

    def _kv_ensure_path(self) -> Path:
        if self._kv_path is None:
            from plugins.astrbot_compat.constants import PLUGINS_DIR
            name = getattr(self, "name", "unknown")
            self._kv_path = PLUGINS_DIR / name / "kv_store.json"
        self._kv_path.parent.mkdir(parents=True, exist_ok=True)
        return self._kv_path

    def _kv_load(self) -> dict[str, Any]:
        p = self._kv_ensure_path()
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _kv_save(self, data: dict[str, Any]) -> None:
        try:
            self._kv_ensure_path().write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("KV save failed: %s", e)


# ======================================================================
# Star — plugin base class
# ======================================================================

class Star(PluginKVStoreMixin):
    """Base class for all astrbot plugins (Stars)."""

    author: str = ""
    name: str = ""
    version: str = ""  # populated from metadata.yaml
    context: Context | None = None
    config: AstrBotConfig | None = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        module = cls.__module__
        _star_classes[module] = cls
        logger.debug(
            "Star subclass registered: %s (module=%s)",
            cls.__name__,
            module,
        )

    def __init__(
        self,
        context: Context,
        config: AstrBotConfig | dict | None = None,
    ):
        self.context = context
        if isinstance(config, dict):
            from astrbot.api.AstrBotConfig import AstrBotConfig as _AC
            config_obj = _AC(
                config_path=Path("."),
                initial=config,
            )
        else:
            config_obj = config
        self.config = config_obj

    async def initialize(self) -> None:
        """Called when plugin is activated. Override in subclass."""
        pass

    async def terminate(self) -> None:
        """Called when plugin is deactivated / reloaded. Override in subclass."""
        pass

    async def text_to_image(self, text: str) -> str:
        """Convert text to an image using the rendering pipeline.

        Returns an absolute file path to the generated image, or empty
        string on failure. The caller can then yield ``event.image_result(path)``.
        """
        try:
            from core.rendering import render_text_to_image, load_font
            img_path = await render_text_to_image(text)
            if img_path:
                logger.debug("text_to_image: generated %s", img_path)
                return str(img_path)
        except ImportError:
            logger.warning("text_to_image: core.rendering not available")
        except Exception as e:
            logger.warning("text_to_image failed: %s", e)
        return ""

    async def html_render(
        self,
        tmpl: str,
        data: dict[str, Any],
        return_url: bool = False,
        **options,
    ) -> str:
        """Render an HTML template to an image.

        Falls back to plain text rendering on failure.
        """
        try:
            from core.rendering import render_html_to_image
            img_path = await render_html_to_image(tmpl, data, return_url=return_url, **options)
            if img_path:
                return str(img_path)
        except ImportError:
            logger.warning("html_render: core.rendering not available")
        except Exception as e:
            logger.warning("html_render failed: %s", e)
        return ""


# ======================================================================
# Star registry helpers
# ======================================================================

def get_registered_star_classes() -> dict[str, type]:
    """Get all Star subclasses registered via __init_subclass__."""
    return dict(_star_classes)


def clear_star_registration(module_name: str) -> None:
    """Remove a module's star from the registry (used on reload)."""
    _star_classes.pop(module_name, None)


def register(plugin_name: str, author: str = "", desc: str = "", version: str = "") -> Callable[[type], type]:
    """AstrBot ``@register()`` decorator — sets metadata on a Star subclass.

    Usage::

        @register("my_plugin", "author", "description", "1.0.0")
        class MyPlugin(Star):
            ...
    """
    def decorator(cls: type) -> type:
        if not hasattr(cls, "name") or not cls.name:
            cls.name = plugin_name
        if not hasattr(cls, "author") or not cls.author:
            cls.author = author
        if not hasattr(cls, "version") or not cls.version:
            cls.version = version
        logger.debug(
            "Plugin registered via @register: name=%s author=%s version=%s",
            plugin_name,
            author,
            version,
        )
        return cls
    return decorator


# ======================================================================
# Context — interface context exposed to plugins
# ======================================================================

# Module-level ref to the bot instance, set by the loader at startup.
_bot_ref: list = []  # hack: single-element list for mutable shared ref


def _set_bot_ref(bot) -> None:
    """Called once by the loader to store the bot instance."""
    _bot_ref.clear()
    _bot_ref.append(bot)


class Context:
    """Shim for AstrBot's Context — interface context exposed to plugins.

    Most advanced methods (LLM, DB, platform) are stubs that raise
    NotImplementedError for v1.
    """

    def __init__(
        self,
        plugin_name: str = "",
        config: AstrBotConfig | None = None,
    ):
        self._plugin_name = plugin_name
        self._config = config

    # --- Config ---

    def get_config(self) -> AstrBotConfig | None:
        return self._config

    # --- Message sending ---

    async def send_message(self, session: str, message_chain: Any) -> bool:
        """Send a message by session ID.

        Session format: ``<platform>:<session_id>``
        Only ``qq:`` is supported in the compat shim.

        ``message_chain`` can be a ``MessageChain``, a list of
        ``BaseMessageComponent``, or a plain string.
        """
        if not _bot_ref:
            logger.warning(
                "send_message: no bot ref available (plugin=%s)",
                self._plugin_name,
            )
            return False

        bot = _bot_ref[0]

        # Parse session string to extract target
        # Format: "qq:group_<group_id>" or "qq:private_<user_id>"
        session_str = str(session)
        if ":" in session_str:
            _, target_part = session_str.split(":", 1)
        else:
            target_part = session_str

        try:
            from plugins.astrbot_compat.loader import convert_chain_to_onebot
            from astrbot.core.message.message_event_result import MessageChain

            if isinstance(message_chain, str):
                ob_msg = message_chain
            elif isinstance(message_chain, MessageChain):
                ob_msg = convert_chain_to_onebot(message_chain)
            elif isinstance(message_chain, list):
                tmp = MessageChain(chain=list(message_chain))
                ob_msg = convert_chain_to_onebot(tmp)
            else:
                ob_msg = str(message_chain)

            await bot.send_msg(
                message_type="group" if "group" in target_part else "private",
                message=ob_msg,
                **({"group_id": int(target_part.split("_")[-1])} if "group" in target_part
                   else {"user_id": int(target_part.split("_")[-1])}),
            )
            return True
        except Exception as e:
            logger.warning(
                "send_message failed (plugin=%s, session=%s): %s",
                self._plugin_name,
                session,
                e,
            )
            return False

    # --- Plugin registration info ---

    def get_registered_star(self, star_name: str) -> Any | None:
        """Look up a registered plugin by name."""
        for cls in _star_classes.values():
            if cls.__name__ == star_name or getattr(cls, "name", "") == star_name:
                return cls
        return None

    def get_all_stars(self) -> list[Any]:
        """Get all registered plugin metadata."""
        return list(_star_classes.values())

    # --- LLM (bridged to the bot's built-in AI Agent) ---

    async def llm_generate(
        self,
        prompt: str = "",
        system_prompt: str | None = None,
        image_urls: list[str] | None = None,
        contexts: list[dict[str, str]] | None = None,
        **kwargs,
    ) -> Any:
        """Call the bot's built-in AI Agent (OpenAI-compatible API) to
        generate a response.

        Returns a dict-like object with at least ``{"role": "assistant", "content": "..."}``.

        Keyword Args:
            prompt: The user message.
            system_prompt: Optional system-level instruction.
            image_urls: Not supported in the current shim.
            contexts: Optional prior conversation turns as ``[{"role": "...", "content": "..."}]``.
        """
        try:
            from plugins.aiagent.config import get_config
            from plugins.aiagent.client import request_chat_completion
        except ImportError:
            raise NotImplementedError(
                f"Bot AI Agent is not available (plugin={self._plugin_name})"
            )

        cfg = get_config()
        messages: list[dict[str, str]] = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        if contexts:
            for ctx in contexts:
                role = ctx.get("role", "user")
                content = ctx.get("content", "")
                if content:
                    messages.append({"role": role, "content": str(content)})

        if prompt:
            # Build the user message, optionally with images embedded
            if image_urls:
                content_parts: list[dict[str, Any]] = [
                    {"type": "text", "text": prompt}
                ]
                for url in image_urls:
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": url},
                    })
                messages.append({"role": "user", "content": content_parts})  # type: ignore
            else:
                messages.append({"role": "user", "content": prompt})

        try:
            reply = await request_chat_completion(cfg, messages)
        except Exception as e:
            logger.warning(
                "llm_generate failed (plugin=%s): %s",
                self._plugin_name,
                e,
            )
            raise

        # Return a dict-like result so callers can access .content etc.
        return {"role": "assistant", "content": reply}

    async def tool_loop_agent(
        self,
        prompt: str = "",
        system_prompt: str | None = None,
        image_urls: list[str] | None = None,
        tools: list[dict[str, Any]] | None = None,
        contexts: list[dict[str, str]] | None = None,
        **kwargs,
    ) -> Any:
        """Run the bot's built-in AI Agent with tool-calling enabled.

        This gives the model access to the registered AI tools
        (web search, file read, wiki lookup, etc.) so it can gather
        information before replying.

        Returns the final assistant reply content.
        """
        try:
            from plugins.aiagent.config import get_config
            from plugins.aiagent.client import request_chat_completion
            from plugins.aiagent.tools import available_tools, execute_tool_call
            from core.ai_tool_registry import AIToolContext
        except ImportError:
            raise NotImplementedError(
                f"Bot AI Agent / tools are not available (plugin={self._plugin_name})"
            )

        cfg = get_config()
        messages: list[dict[str, Any]] = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        if contexts:
            for ctx in contexts:
                role = ctx.get("role", "user")
                content = ctx.get("content", "")
                if content:
                    messages.append({"role": role, "content": content})

        if prompt:
            messages.append({"role": "user", "content": prompt})

        # The bot's tool system uses its own registry, so we inject
        # whatever tools the caller asked for alongside the default set.
        tool_context = AIToolContext(agent_config=cfg)
        all_tools = available_tools(cfg, tool_context)

        # If the caller explicitly passed tool definitions, try to merge them
        if tools:
            existing_names = {t.get("function", {}).get("name") for t in all_tools}
            for t in tools:
                name = t.get("function", {}).get("name", "") if isinstance(t, dict) else ""
                if name and name not in existing_names:
                    all_tools.append(t)

        # Temporarily inject extra tools into the config for this call
        if all_tools:
            cfg = dict(cfg)
            cfg["tools"] = dict(cfg.get("tools", {}))
            cfg["tools"]["extra"] = all_tools

        try:
            reply = await request_chat_completion(cfg, messages, tool_context)
        except Exception as e:
            logger.warning(
                "tool_loop_agent failed (plugin=%s): %s",
                self._plugin_name,
                e,
            )
            raise

        return {"role": "assistant", "content": reply}

    def get_llm_tool_manager(self) -> Any:
        raise NotImplementedError(
            f"LLM tools not available in compat shim (plugin={self._plugin_name})"
        )

    def activate_llm_tool(self, name: str) -> bool:
        logger.warning("activate_llm_tool stub (plugin=%s, tool=%s)", self._plugin_name, name)
        return False

    def deactivate_llm_tool(self, name: str) -> bool:
        logger.warning("deactivate_llm_tool stub (plugin=%s, tool=%s)", self._plugin_name, name)
        return False

    # --- Providers (stubs) ---

    def get_all_providers(self) -> list:
        return []

    def get_all_tts_providers(self) -> list:
        return []

    def get_all_stt_providers(self) -> list:
        return []

    def get_all_embedding_providers(self) -> list:
        return []

    def get_using_provider(self, umo: str | None = None) -> Any:
        return None

    def get_using_tts_provider(self, umo: str | None = None) -> Any:
        return None

    def get_using_stt_provider(self, umo: str | None = None) -> Any:
        return None

    def get_provider_by_id(self, provider_id: str) -> Any:
        return None

    # --- DB (stub) ---

    def get_db(self) -> Any:
        raise NotImplementedError(
            f"DB access not available in compat shim (plugin={self._plugin_name})"
        )

    # --- LLM tool registration (stubs) ---

    def add_llm_tools(self, *tools) -> None:
        logger.warning(
            "add_llm_tools stub (plugin=%s, count=%d)",
            self._plugin_name,
            len(tools),
        )

    def register_llm_tool(self, name: str, func_args: list, desc: str, func_obj: Any) -> None:
        logger.warning(
            "register_llm_tool stub (plugin=%s, tool=%s)",
            self._plugin_name,
            name,
        )

    def unregister_llm_tool(self, name: str) -> None:
        logger.warning(
            "unregister_llm_tool stub (plugin=%s, tool=%s)",
            self._plugin_name,
            name,
        )

    # --- Web API (stub) ---

    def register_web_api(self, route: str, view_handler: Any, methods: list[str], desc: str) -> None:
        logger.warning(
            "register_web_api is not supported in compat shim "
            "(plugin=%s, route=%s)",
            self._plugin_name,
            route,
        )

    # --- Deprecated stubs ---

    def register_commands(self, *args, **kwargs) -> None:
        pass

    def register_task(self, task: Any, desc: str = "") -> None:
        pass

    def get_platform(self, *args, **kwargs) -> None:
        return None

    def get_platform_inst(self, platform_id: str) -> None:
        return None
