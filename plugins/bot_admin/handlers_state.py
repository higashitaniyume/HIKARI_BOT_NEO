"""State handler mixin — GET endpoints that read and return state.

Imports helpers from sibling modules; calls base methods (self._send_json,
self._query_params, etc.) on the combined BotAdminHandler via MRO.
"""

from __future__ import annotations

import logging

from . import astrbot_ops
from .activities import activity_state
from .aiagent_memory import _read_memory_file, aiagent_memory_state
from .operations import (
    _access_rules_state,
    _list_logs,
    _list_plugin_configs,
    _push_config_state,
    _rss_config_state,
)
from .settings import _aiagent_config_state, _aiagent_quota_state, _tts_config_state
from .stickers import _inbox_state, _pack_state, _voice_state
from .system_probe import system_probe_state
from core.runtime_info import runtime_info_state

logger = logging.getLogger("HikariBot.BotAdmin")


class StateHandlerMixin:
    """Mixin providing GET state-reading handlers."""

    # ---- GET API exact paths ------------------------------------------------

    def _handle_api_state(self) -> None:
        self._send_json(_pack_state())

    def _handle_system_probe(self) -> None:
        self._send_json(system_probe_state())

    def _handle_activities(self) -> None:
        self._send_json(activity_state())

    def _handle_version(self) -> None:
        self._send_json(runtime_info_state())

    def _handle_api_inbox(self) -> None:
        self._send_json(_inbox_state())

    def _handle_voice_state(self) -> None:
        self._send_json(_voice_state())

    def _handle_tts_config_get(self) -> None:
        self._send_json(_tts_config_state())

    def _handle_aiagent_config_get(self) -> None:
        self._send_json(_aiagent_config_state())

    def _handle_aiagent_memory(self) -> None:
        file_param = self._query_params.get("file", [None])[0]
        if file_param:
            self._send_json(_read_memory_file(file_param))
        else:
            self._send_json(aiagent_memory_state())

    def _handle_aiagent_quota(self) -> None:
        try:
            self._send_json(_aiagent_quota_state())
        except Exception as e:
            logger.exception("读取 AI 配额失败: %s", e)
            self._send_json({"error": "读取 AI 配额失败，请检查服务日志。"}, 500)

    def _handle_push_config_get(self) -> None:
        try:
            self._send_json(_push_config_state())
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("读取推送配置失败: %s", e)
            self._send_json({"error": "读取推送配置失败，请检查服务日志。"}, 500)

    def _handle_rss_config_get(self) -> None:
        try:
            self._send_json(_rss_config_state())
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("读取 RSS 订阅配置失败: %s", e)
            self._send_json({"error": "读取 RSS 订阅配置失败，请检查服务日志。"}, 500)

    def _handle_access_rules_get(self) -> None:
        try:
            self._send_json(_access_rules_state())
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("读取权限规则失败: %s", e)
            self._send_json({"error": "读取权限规则失败，请检查服务日志。"}, 500)

    def _handle_configs_list(self) -> None:
        self._send_json(_list_plugin_configs())

    def _handle_logs_list(self) -> None:
        self._send_json(_list_logs())

    def _handle_astrbot_plugins(self) -> None:
        try:
            self._send_json(astrbot_ops.list_plugins())
        except Exception as e:
            logger.exception("读取AstrBot插件列表失败: %s", e)
            self._send_json({"error": "读取插件列表失败，请检查服务日志。"}, 500)
