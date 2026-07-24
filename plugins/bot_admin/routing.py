"""Route registry for bot_admin HTTP server.

Uses werkzeug.routing for declarative URL matching, replacing the
previous ~60 if/elif branches in do_GET/do_POST/do_DELETE.

Also provides register_plugin_page() so AstrBot plugin compat shim
can register dynamic web pages from loaded plugins.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from werkzeug.routing import Map, Rule
from werkzeug.exceptions import NotFound

logger = logging.getLogger("HikariBot.BotAdmin")

# ---------------------------------------------------------------------------
# Route definition
# ---------------------------------------------------------------------------

@dataclass
class RouteEntry:
    """A single route: HTTP methods, URL pattern, endpoint id, handler method."""

    methods: set[str]
    rule: str
    endpoint: str
    handler_name: str
    auth: bool = True


# Every fixed route in the admin panel, in priority order.
# werkzeug sorts rules by specificity automatically, but declaring
# more-specific ones first is a safe habit.
ROUTE_DEFS: list[RouteEntry] = [
    # ===== Public (auth=False) ==========================================
    RouteEntry({"GET"}, "/static/<path:relative>", "static", "_handle_static", auth=False),
    RouteEntry({"GET"}, "/login", "login_page", "_handle_login_page", auth=False),
    RouteEntry({"POST"}, "/login", "login_action", "_handle_login_action", auth=False),
    RouteEntry({"GET"}, "/logout", "logout", "_handle_logout", auth=False),

    # ===== Index page (auth=True) =======================================
    RouteEntry({"GET"}, "/", "index", "_handle_index"),
    RouteEntry({"GET"}, "/index.html", "index_html", "_handle_index"),

    # ===== GET API — exact paths =========================================
    RouteEntry({"GET"}, "/api/state", "api_state", "_handle_api_state"),
    RouteEntry({"GET"}, "/api/system-probe", "api_system_probe", "_handle_system_probe"),
    RouteEntry({"GET"}, "/api/activities", "api_activities", "_handle_activities"),
    RouteEntry({"GET"}, "/api/version", "api_version", "_handle_version"),
    RouteEntry({"GET"}, "/api/inbox", "api_inbox", "_handle_api_inbox"),
    RouteEntry({"GET"}, "/api/voice-state", "api_voice_state", "_handle_voice_state"),
    RouteEntry({"GET"}, "/api/tts-config", "api_tts_config_get", "_handle_tts_config_get"),
    RouteEntry({"GET"}, "/api/aiagent-config", "api_aiagent_config_get", "_handle_aiagent_config_get"),
    RouteEntry({"GET"}, "/api/aiagent-memory", "api_aiagent_memory", "_handle_aiagent_memory"),
    RouteEntry({"GET"}, "/api/push-config", "api_push_config_get", "_handle_push_config_get"),
    RouteEntry({"GET"}, "/api/rss-config", "api_rss_config_get", "_handle_rss_config_get"),
    RouteEntry({"GET"}, "/api/access-rules", "api_access_rules_get", "_handle_access_rules_get"),
    RouteEntry({"GET"}, "/api/configs", "api_configs_list", "_handle_configs_list"),
    RouteEntry({"GET"}, "/api/logs", "api_logs_list", "_handle_logs_list"),
    RouteEntry({"GET"}, "/api/astrbot/plugins", "api_astrbot_plugins", "_handle_astrbot_plugins"),

    # ===== GET API — path parameters =====================================
    # /api/packs/<name>/download MUST be before /api/packs/<name>
    RouteEntry({"GET"}, "/api/packs/<name>/download", "api_pack_download", "_handle_pack_download"),
    RouteEntry({"GET"}, "/api/packs/<name>", "api_pack_detail", "_handle_pack_detail"),
    RouteEntry({"GET"}, "/api/configs/<name>", "api_configs_detail", "_handle_configs_detail"),
    RouteEntry({"GET"}, "/api/logs/<name>", "api_logs_detail", "_handle_logs_detail"),
    RouteEntry({"GET"}, "/api/astrbot/plugins/<name>", "api_astrbot_plugin_detail", "_handle_astrbot_plugin_detail"),
    RouteEntry({"GET"}, "/api/stickers/<sticker_id>", "api_sticker", "_handle_sticker"),
    RouteEntry({"GET"}, "/api/uploads/<job_id>", "api_upload_status", "_handle_upload_status"),

    # ===== GET API — prefix+suffix patterns (now explicit Rules) =========
    RouteEntry({"GET"}, "/api/inbox/<item_id>/image", "api_inbox_image", "_handle_inbox_image"),
    RouteEntry({"GET"}, "/api/voices/<voice_id>/file", "api_voice_file", "_handle_voice_file"),

    # ===== POST API — exact paths ========================================
    RouteEntry({"POST"}, "/api/tts-config", "api_tts_config_save", "_handle_tts_config_save"),
    RouteEntry({"POST"}, "/api/aiagent-config", "api_aiagent_config_save", "_handle_aiagent_config_save"),
    RouteEntry({"POST"}, "/api/aiagent-memory/summarize", "api_aiagent_memory_summarize", "_handle_aiagent_memory_summarize"),
    RouteEntry({"POST"}, "/api/push-config", "api_push_config_save", "_handle_push_config_save"),
    RouteEntry({"POST"}, "/api/rss-config", "api_rss_config_save", "_handle_rss_config_save"),
    RouteEntry({"POST"}, "/api/push-run", "api_push_run", "_handle_push_run"),
    RouteEntry({"POST"}, "/api/access-rules", "api_access_rules_save", "_handle_access_rules_save"),
    RouteEntry({"POST"}, "/api/astrbot/plugins/save-config", "api_astrbot_save_config", "_handle_astrbot_save_config"),
    RouteEntry({"POST"}, "/api/astrbot/plugins/reload", "api_astrbot_reload", "_handle_astrbot_reload"),
    RouteEntry({"POST"}, "/api/astrbot/plugins/remove", "api_astrbot_remove", "_handle_astrbot_remove"),
    RouteEntry({"POST"}, "/api/astrbot/load", "api_astrbot_load", "_handle_astrbot_load"),
    RouteEntry({"POST"}, "/api/astrbot/rebuild-env", "api_astrbot_rebuild_env", "_handle_astrbot_rebuild_env"),
    RouteEntry({"POST"}, "/api/astrbot/discover", "api_astrbot_discover", "_handle_astrbot_discover"),
    RouteEntry({"POST"}, "/api/astrbot/upload-zip", "api_astrbot_upload_zip", "_handle_astrbot_upload_zip"),
    RouteEntry({"POST"}, "/api/voice-keywords", "api_voice_keywords_add", "_handle_voice_keywords_add"),
    RouteEntry({"POST"}, "/api/voices", "api_voices_upload", "_handle_voices_upload"),
    RouteEntry({"POST"}, "/api/keywords", "api_keywords_add", "_handle_keywords_add"),
    RouteEntry({"POST"}, "/api/pack-stickers/delete", "api_pack_stickers_delete", "_handle_pack_stickers_delete"),
    RouteEntry({"POST"}, "/api/pack-stickers/move", "api_pack_stickers_move", "_handle_pack_stickers_move"),
    RouteEntry({"POST"}, "/api/tg-stickers", "api_tg_stickers", "_handle_tg_stickers"),
    RouteEntry({"POST"}, "/api/inbox/assign", "api_inbox_assign", "_handle_inbox_assign"),
    RouteEntry({"POST"}, "/api/inbox/delete", "api_inbox_delete", "_handle_inbox_delete"),

    # ===== POST API — path parameters ====================================
    RouteEntry({"POST"}, "/api/configs/<name>", "api_configs_save", "_handle_configs_save"),

    # ===== POST — multipart upload (end-of-chain) =========================
    RouteEntry({"POST"}, "/upload", "upload_html", "_handle_upload_html"),
    RouteEntry({"POST"}, "/api/uploads", "api_uploads", "_handle_api_uploads"),

    # ===== DELETE =========================================================
    RouteEntry({"DELETE"}, "/api/packs", "api_packs_delete", "_handle_packs_delete"),
    RouteEntry({"DELETE"}, "/api/voices", "api_voices_delete", "_handle_voices_delete"),
    RouteEntry({"DELETE"}, "/api/voice-keywords", "api_voice_keywords_delete", "_handle_voice_keywords_delete"),
    RouteEntry({"DELETE"}, "/api/keywords", "api_keywords_delete", "_handle_keywords_delete"),

    # ===== Plugin Pages (dynamic, catch-all) ==============================
    RouteEntry({"GET", "POST"}, "/plugin/<plugin_name>/<path:rest>", "plugin_page", "_handle_plugin_page"),
    RouteEntry({"GET"}, "/api/astrbot/plugin-pages", "api_plugin_pages_list", "_handle_plugin_pages_list"),
]

# ---------------------------------------------------------------------------
# Build werkzeug Map
# ---------------------------------------------------------------------------

_rules: list[Rule] = []
_endpoint_meta: dict[str, RouteEntry] = {}

for _rd in ROUTE_DEFS:
    _rules.append(Rule(_rd.rule, methods=_rd.methods, endpoint=_rd.endpoint))
    _endpoint_meta[_rd.endpoint] = _rd

_map = Map(_rules)


def get_endpoint_meta(endpoint: str) -> RouteEntry | None:
    """Return the RouteEntry for a given endpoint, or None."""
    return _endpoint_meta.get(endpoint)


# ---------------------------------------------------------------------------
# Plugin page registry (for AstrBot compat register_web_api)
# ---------------------------------------------------------------------------

# {plugin_name: {route: {"handler": ..., "methods": [...], "desc": "..."}}}
_plugin_pages: dict[str, dict[str, dict[str, Any]]] = {}


def register_plugin_page(
    plugin_name: str,
    route: str,
    handler: Any,
    methods: list[str],
    desc: str = "",
) -> bool:
    """Register a plugin-provided web page. Called by astrbot compat shim.

    Args:
        plugin_name: The plugin's directory name.
        route: The URL path within the plugin's namespace (e.g. "/admin").
        handler: A callable that receives the request context.
        methods: HTTP methods as strings (e.g. ["GET", "POST"]).
        desc: Human-readable description shown in the admin sidebar.

    Returns:
        True on success.
    """
    route = "/" + route.lstrip("/")
    if plugin_name not in _plugin_pages:
        _plugin_pages[plugin_name] = {}
    _plugin_pages[plugin_name][route] = {
        "handler": handler,
        "methods": [m.upper() for m in methods],
        "desc": desc,
    }
    logger.info(
        "Plugin page registered: %s -> /plugin/%s%s (methods=%s)",
        plugin_name,
        plugin_name,
        route,
        methods,
    )
    return True


def get_plugin_pages() -> dict[str, list[dict[str, Any]]]:
    """Return all registered plugin pages, for the admin sidebar API."""
    result: dict[str, list[dict[str, Any]]] = {}
    for plugin_name, routes in _plugin_pages.items():
        pages: list[dict[str, Any]] = []
        for route, info in routes.items():
            pages.append({
                "route": route,
                "full_path": f"/plugin/{plugin_name}{route}",
                "methods": info["methods"],
                "desc": info["desc"],
            })
        result[plugin_name] = pages
    return result


def lookup_plugin_page(plugin_name: str, route: str) -> dict[str, Any] | None:
    """Look up a registered plugin page handler. Returns None if not found."""
    pages = _plugin_pages.get(plugin_name, {})
    return pages.get("/" + route.lstrip("/"))


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def dispatch(handler_instance: Any, method: str, path: str) -> None:
    """Match and dispatch a single HTTP request.

    Args:
        handler_instance: The BotAdminHandler instance handling this request.
        method: HTTP method string (GET, POST, DELETE, OPTIONS).
        path: The full request path including query string (e.g. "/api/state?k=v").
    """
    from urllib.parse import parse_qs, urlparse

    from .pages import _html_page

    parsed = urlparse(path)
    path_info = parsed.path

    # Make query params available to handlers via self._query_params
    handler_instance._query_params = parse_qs(parsed.query)

    adapter = _map.bind("localhost", path_info=path_info)
    try:
        endpoint, kwargs = adapter.match(method=method)
    except NotFound:
        handler_instance._send_html(_html_page("页面不存在。"), 404)
        return

    meta = _endpoint_meta.get(endpoint)
    if meta is None:
        handler_instance._send_html(_html_page("内部错误：未注册的端点。"), 500)
        return

    # ---- auth check ----
    if meta.auth and not handler_instance._is_authenticated():
        if endpoint.startswith("api_"):
            handler_instance._unauthorized_json()
        else:
            handler_instance._send_login()
        return

    # ---- call handler ----
    handler_method = getattr(handler_instance, meta.handler_name, None)
    if handler_method is None:
        logger.error(
            "Handler method %r not found on handler instance for endpoint %r",
            meta.handler_name,
            endpoint,
        )
        handler_instance._send_html(_html_page("内部错误。"), 500)
        return

    handler_method(**kwargs)
