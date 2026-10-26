"""Terminal handler mixin — 后台「终端」页接口。"""

from __future__ import annotations

import logging
from typing import Any

from .terminal_admin import read_terminal_log, terminal_overview

logger = logging.getLogger("HikariBot.BotAdmin")


def _int_param(params: dict[str, Any], name: str, default: int, low: int, high: int) -> int:
    raw = (params.get(name) or [""])[0]
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(value, high))


def _text_param(params: dict[str, Any], name: str, limit: int = 120) -> str:
    raw = (params.get(name) or [""])[0]
    return str(raw or "").strip()[:limit]


class TerminalHandlerMixin:
    """Mixin providing remote terminal overview / log handlers."""

    def _handle_terminal_overview(self) -> None:
        params = getattr(self, "_query_params", {}) or {}
        try:
            self._send_json(
                terminal_overview(
                    limit=_int_param(params, "limit", 100, 1, 500),
                    query=_text_param(params, "q"),
                    action=_text_param(params, "action", 32),
                )
            )
        except Exception as e:  # noqa: BLE001 - 后台错误统一返回
            logger.exception("读取终端概览失败: %s", e)
            self._send_json({"error": "读取终端记录失败，请检查服务日志。"}, 500)

    def _handle_terminal_log(self) -> None:
        params = getattr(self, "_query_params", {}) or {}
        try:
            self._send_json(
                read_terminal_log(
                    _text_param(params, "session", 32),
                    _text_param(params, "name", 120),
                )
            )
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except FileNotFoundError as e:
            self._send_json({"error": str(e)}, 404)
        except Exception as e:  # noqa: BLE001
            logger.exception("读取终端日志失败: %s", e)
            self._send_json({"error": "读取命令输出失败，请检查服务日志。"}, 500)
