"""Shared runtime state for the AstrBot compatibility layer."""

from __future__ import annotations

import re
import time
from pathlib import Path
from types import ModuleType
from typing import Any

from astrbot.api.AstrBotConfig import AstrBotConfig
from astrbot.api.star import Context, Star

from core.command_router import CommandSpec


class PluginHandle:
    """Tracks a loaded AstrBot plugin's runtime state."""

    def __init__(
        self,
        name: str,
        display_name: str,
        module_path: Path,
        module_prefix: str,
        module: ModuleType,
        star_class: type[Star],
        instance: Star,
        ctx: Context,
        config_obj: AstrBotConfig,
    ):
        self.name = name
        self.display_name = display_name
        self.module_path = module_path
        self.module_prefix = module_prefix
        self.module = module
        self.star_class = star_class
        self.instance = instance
        self.ctx = ctx
        self.config_obj = config_obj
        self.command_names: list[str] = []
        self.command_specs: list[CommandSpec] = []
        self.regex_handlers: list[RegexMatcher] = []
        self.on_message_handlers: list[OnMsgHandler] = []
        self._command_aliases: dict[str, list[str]] = {}
        self._load_timestamp = time.monotonic()

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
    def __init__(self, plugin_name: str, pattern: re.Pattern, handler: Any):
        self.plugin_name = plugin_name
        self.pattern = pattern
        self.handler = handler


class OnMsgHandler:
    def __init__(self, plugin_name: str, handler: Any):
        self.plugin_name = plugin_name
        self.handler = handler


loaded_plugins: dict[str, PluginHandle] = {}
regex_matchers: list[RegexMatcher] = []
on_message_handlers: list[OnMsgHandler] = []
