"""Plugin page handler mixin — AstrBot plugin web page dispatch.

Imports helpers from sibling modules; calls base methods (self._send_json,
self.command, etc.) on the combined BotAdminHandler via MRO.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("HikariBot.BotAdmin")


class PluginPageHandlerMixin:
    """Mixin providing plugin web page handlers (dynamic, catch-all)."""

    def _handle_plugin_page(self, plugin_name: str, rest: str) -> None:
        """Dispatch a request to a registered AstrBot plugin web page."""
        from .routing import lookup_plugin_page

        page = lookup_plugin_page(plugin_name, rest)
        if page is None:
            self._send_json({"error": f"插件页面不存在: /plugin/{plugin_name}/{rest}"}, 404)
            return

        # Check that the method is allowed
        if self.command not in page["methods"]:
            self._send_json({
                "error": f"方法 {self.command} 不允许，支持: {', '.join(page['methods'])}",
            }, 405)
            return

        # Forward to plugin handler
        handler = page["handler"]
        try:
            handler(self)
        except Exception as e:
            logger.exception("插件页面处理失败 [%s/%s]: %s", plugin_name, rest, e)
            self._send_json({"error": "插件页面处理失败。"}, 500)

    def _handle_plugin_pages_list(self) -> None:
        """Return the list of registered plugin pages for the admin sidebar."""
        from .routing import get_plugin_pages
        self._send_json(get_plugin_pages())
