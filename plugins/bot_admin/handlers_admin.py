"""AstrBot admin handler mixin — plugin management endpoints.

Imports helpers from sibling modules; calls base methods (self._read_json_body,
self._send_json, self._parse_multipart_form, etc.) on the combined
BotAdminHandler via MRO.
"""

from __future__ import annotations

import logging

from . import astrbot_ops

logger = logging.getLogger("HikariBot.BotAdmin")


class AdminHandlerMixin:
    """Mixin providing AstrBot plugin management handlers."""

    def _handle_astrbot_save_config(self) -> None:
        try:
            data = self._read_json_body()
            name = str(data.get("name", "")).strip()
            config = data.get("config", {})
            if not name:
                raise ValueError("插件名不能为空。")
            result = astrbot_ops.save_plugin_config(name, config)
            result["message"] = "配置已保存。"
            self._send_json(result)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("保存AstrBot插件配置失败: %s", e)
            self._send_json({"error": "保存配置失败，请检查服务日志。"}, 500)

    def _handle_astrbot_reload(self) -> None:
        try:
            data = self._read_json_body()
            name = str(data.get("name", "")).strip()
            if not name:
                raise ValueError("插件名不能为空。")
            result = astrbot_ops.reload_plugin(name)
            result["message"] = "插件已重新加载。"
            self._send_json(result)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("重载AstrBot插件失败: %s", e)
            self._send_json({"error": "重载插件失败，请检查服务日志。"}, 500)

    def _handle_astrbot_remove(self) -> None:
        try:
            data = self._read_json_body()
            name = str(data.get("name", "")).strip()
            if not name:
                raise ValueError("插件名不能为空。")
            result = astrbot_ops.remove_plugin(name)
            result["message"] = "插件已卸载。"
            self._send_json(result)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("卸载AstrBot插件失败: %s", e)
            self._send_json({"error": "卸载插件失败，请检查服务日志。"}, 500)

    def _handle_astrbot_load(self) -> None:
        try:
            data = self._read_json_body()
            plugin_path = str(data.get("path", "")).strip()
            plugin_name = str(data.get("name", "")).strip() or None
            if not plugin_path:
                raise ValueError("插件路径不能为空。")
            result = astrbot_ops.load_plugin_from_path(plugin_path, plugin_name)
            result["message"] = "插件已加载。"
            self._send_json(result)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("加载AstrBot插件失败: %s", e)
            self._send_json({"error": "加载插件失败，请检查服务日志。"}, 500)

    def _handle_astrbot_rebuild_env(self) -> None:
        try:
            result = astrbot_ops.rebuild_plugin_env()
            result["message"] = "公共虚拟环境已重建。"
            self._send_json(result)
        except Exception as e:
            logger.exception("重建AstrBot虚拟环境失败: %s", e)
            self._send_json({"error": "重建虚拟环境失败，请检查服务日志。"}, 500)

    def _handle_astrbot_discover(self) -> None:
        try:
            from plugins.astrbot_compat.manager import discover_plugins
            dirs = discover_plugins()
            self._send_json({
                "plugins": [str(d) for d in dirs],
                "count": len(dirs),
            })
        except Exception as e:
            logger.exception("发现AstrBot插件失败: %s", e)
            self._send_json({"error": "发现插件失败，请检查服务日志。"}, 500)

    def _handle_astrbot_upload_zip(self) -> None:
        try:
            fields, files = self._parse_multipart_form()
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
            return

        file_infos = files.get("plugin_archive", [])
        if not file_infos:
            self._send_json({"error": "请选择要上传的 zip 文件。"}, 400)
            return

        archive_info = file_infos[0]
        archive_content = archive_info.get("content", b"")
        filename = archive_info.get("filename", "plugin.zip")
        plugin_name = fields.get("plugin_name", "").strip() or None

        if not archive_content:
            self._send_json({"error": "上传内容为空。"}, 400)
            return

        try:
            result = astrbot_ops.upload_and_load_plugin(archive_content, filename, plugin_name)
            self._send_json(result)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("上传AstrBot插件失败: %s", e)
            self._send_json({"error": "上传插件失败，请检查服务日志。"}, 500)
