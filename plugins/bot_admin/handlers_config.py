"""Config handler mixin — POST endpoints that save configuration.

Imports helpers from sibling modules; calls base methods (self._read_json_body,
self._send_json, etc.) on the combined BotAdminHandler via MRO.
"""

from __future__ import annotations

import asyncio
import logging

from .aiagent_memory import trigger_summarize
from .operations import (
    _push_run_payload,
    _write_access_rules,
    _write_plugin_config,
    _write_push_config,
    _write_rss_config,
)
from .parsing import _parse_float, _parse_str
from .settings import (
    _aiagent_config_state,
    _aiagent_quota_state,
    _tts_config_state,
    _update_aiagent_config,
    _update_aiagent_quota,
    _update_tts_config,
)
from plugins.aiagent.quota import reset_all_usage, reset_scope
from plugins.push_framework import submit_manual_push

logger = logging.getLogger("HikariBot.BotAdmin")


class ConfigHandlerMixin:
    """Mixin providing POST config-saving handlers."""

    def _handle_configs_save(self, name: str) -> None:
        try:
            data = self._read_json_body()
            content = str(data.get("content", ""))
            result = _write_plugin_config(name, content)
            self._send_json({"config": result, "message": "配置已保存。"})
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("保存插件配置失败: %s", e)
            self._send_json({"error": "保存插件配置失败，请检查服务日志。"}, 500)

    def _handle_tts_config_save(self) -> None:
        try:
            data = self._read_json_body()
            _update_tts_config(data)
            payload = _tts_config_state()
            payload["message"] = "TTS 设置已保存。"
            self._send_json(payload)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("保存 TTS 设置失败: %s", e)
            self._send_json({"error": "保存 TTS 设置失败，请检查服务日志。"}, 500)

    def _handle_aiagent_config_save(self) -> None:
        try:
            data = self._read_json_body()
            _update_aiagent_config(data)
            payload = _aiagent_config_state()
            payload["message"] = "AI Agent 设置已保存。"
            self._send_json(payload)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("保存 AI Agent 设置失败: %s", e)
            self._send_json({"error": "保存 AI Agent 设置失败，请检查服务日志。"}, 500)

    def _handle_aiagent_quota_save(self) -> None:
        try:
            data = self._read_json_body()
            payload = _update_aiagent_quota(data)
            payload["message"] = "AI 配额设置已保存。"
            self._send_json(payload)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("保存 AI 配额设置失败: %s", e)
            self._send_json({"error": "保存 AI 配额设置失败，请检查服务日志。"}, 500)

    def _handle_aiagent_quota_reset(self) -> None:
        try:
            data = self._read_json_body()
            scope = str(data.get("scope") or "").strip()
            if not scope:
                count = reset_all_usage()
                self._send_json({"message": f"已重置全部 {count} 个 scope 的用量。"})
                return
            if not reset_scope(scope):
                self._send_json({"error": f"没有找到该 scope 的用量记录: {scope}"}, 404)
                return
            self._send_json({"scope": scope, "message": f"已重置 {scope} 的用量。"})
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("重置 AI 配额失败: %s", e)
            self._send_json({"error": "重置 AI 配额失败，请检查服务日志。"}, 500)

    def _handle_aiagent_memory_summarize(self) -> None:
        try:
            data = self._read_json_body()
            file_param = str(data.get("file", "")).strip()
            if not file_param:
                self._send_json({"error": "file 参数不能为空。"}, 400)
                return
            result = asyncio.run(trigger_summarize(file_param))
            self._send_json(result)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("触发记忆总结失败: %s", e)
            self._send_json({"error": "触发记忆总结失败，请检查服务日志。"}, 500)

    def _handle_push_config_save(self) -> None:
        try:
            data = self._read_json_body()
            self._send_json(_write_push_config(data))
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("保存推送配置失败: %s", e)
            self._send_json({"error": "保存推送配置失败，请检查服务日志。"}, 500)

    def _handle_rss_config_save(self) -> None:
        try:
            data = self._read_json_body()
            self._send_json(_write_rss_config(data))
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("保存 RSS 订阅配置失败: %s", e)
            self._send_json({"error": "保存 RSS 订阅配置失败，请检查服务日志。"}, 500)

    def _handle_access_rules_save(self) -> None:
        try:
            data = self._read_json_body()
            self._send_json(_write_access_rules(data))
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("保存权限规则失败: %s", e)
            self._send_json({"error": "保存权限规则失败，请检查服务日志。"}, 500)

    def _handle_push_run(self) -> None:
        try:
            data = self._read_json_body()
            job_id = _parse_str(data.get("job_id"), max_length=80)
            if not job_id:
                raise ValueError("推送任务 ID 不能为空。")
            timeout_seconds = _parse_float(
                data.get("timeout_seconds", 300),
                300.0,
                minimum=1.0,
                maximum=1800.0,
            )
            result = submit_manual_push(job_id, timeout_seconds=timeout_seconds)
            if result is None:
                self._send_json({"error": f"没有找到推送任务：{job_id}"}, 404)
                return
            self._send_json({
                "result": _push_run_payload(result),
                "message": "推送任务已执行。",
            })
        except TimeoutError as e:
            self._send_json({"error": str(e)}, 504)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except RuntimeError as e:
            self._send_json({"error": str(e)}, 409)
        except Exception as e:
            logger.exception("手动触发推送失败: %s", e)
            self._send_json({"error": "手动触发推送失败，请检查服务日志。"}, 500)
