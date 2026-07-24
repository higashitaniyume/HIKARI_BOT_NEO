from __future__ import annotations

import asyncio
import hmac
import json
import logging
import mimetypes
import re
import threading
from email.parser import BytesParser
from email.policy import default as email_policy
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from core.runtime_info import runtime_info_state
from plugins import sticker_inbox
from plugins import sticker_library
from plugins import voice_library
from plugins.push_framework import submit_manual_push
from plugins.tg_sticker_parser.tg_api import extract_sticker_set_names

from . import astrbot_ops
from .activities import activity_state
from .aiagent_memory import _read_memory_file, aiagent_memory_state, trigger_summarize
from .archives import _archive_download_name, _create_pack_archive
from .auth import _auth_enabled, _auth_password, _make_session_token, _session_ttl_seconds, _valid_session_token
from .constants import _COOKIE_NAME, _MAX_LOG_TAIL_BYTES, _STATIC_ROOT, MAX_UPLOAD_FILES, MAX_VOICE_UPLOAD_FILES
from .operations import (
    _access_rules_state,
    _list_logs,
    _list_plugin_configs,
    _push_config_state,
    _push_run_payload,
    _read_log_tail,
    _read_plugin_config,
    _rss_config_state,
    _write_access_rules,
    _write_plugin_config,
    _write_push_config,
    _write_rss_config,
)
from .pages import _html_page, _login_page
from .parsing import _json_bytes, _parse_float, _parse_str
from .settings import _aiagent_config_state, _tts_config_state, _update_aiagent_config, _update_tts_config
from .stickers import (
    _add_trigger_keyword,
    _inbox_state,
    _pack_detail_state,
    _pack_state,
    _remove_trigger_keyword,
    _split_keywords,
    _voice_state,
)
from .system_probe import system_probe_state
from .uploads import _get_upload_job, _new_upload_job, _process_tg_sticker_link, _process_upload_files, _process_voice_uploads, _update_upload_job
from .utils import _safe_pack_name, _safe_voice_name

logger = logging.getLogger("HikariBot.BotAdmin")
_API_TOKEN_HEADERS = ("X-Admin-Token", "X-Hikari-Admin-Token", "Token")


class BotAdminHandler(BaseHTTPRequestHandler):
    server_version = "HikariBotAdmin/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("[BotAdmin] " + fmt, *args)

    def send_response(self, code: int, message: str | None = None) -> None:
        super().send_response(code, message)
        origin = self.headers.get("Origin")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
        else:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS, PUT, PATCH")
        request_headers = self.headers.get("Access-Control-Request-Headers")
        if request_headers:
            self.send_header("Access-Control-Allow-Headers", request_headers)
        else:
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Admin-Token, X-Hikari-Admin-Token, Token")

    def _send_html(self, body: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self._write_body(body)

    def _write_body(self, body: bytes) -> None:
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            logger.info("[BotAdmin] 客户端在响应写入前断开连接")

    def _redirect(self, location: str, cookie: str | None = None) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def _is_authenticated(self) -> bool:
        if not _auth_enabled():
            return True
        if self._is_api_request() and self._is_valid_api_token():
            return True
        cookie_header = self.headers.get("Cookie", "")
        cookie = SimpleCookie(cookie_header)
        morsel = cookie.get(_COOKIE_NAME)
        return bool(morsel and _valid_session_token(morsel.value))

    def _is_api_request(self) -> bool:
        return urlparse(self.path).path.startswith("/api/")

    def _api_token_from_headers(self) -> str:
        authorization = self.headers.get("Authorization", "").strip()
        if authorization.lower().startswith("bearer "):
            return authorization[7:].strip()
        for header_name in _API_TOKEN_HEADERS:
            token = self.headers.get(header_name, "").strip()
            if token:
                return token
        return ""

    def _is_valid_api_token(self) -> bool:
        token = self._api_token_from_headers()
        return bool(token) and hmac.compare_digest(token, _auth_password())

    def _send_login(self, message: str = "", status: int = 200) -> None:
        self._send_html(_login_page(message), status)

    def _unauthorized_json(self) -> None:
        self._send_json({"error": "请先登录。"}, 401)

    def _read_form_body(self) -> dict[str, str]:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as e:
            raise ValueError("请求格式错误：Content-Length 无效。") from e
        body = self.rfile.read(max(content_length, 0)).decode("utf-8", errors="replace")
        values = parse_qs(body)
        return {key: value[-1] for key, value in values.items() if value}

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = _json_bytes(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self._write_body(body)

    def _send_download_file(self, path: Path, download_name: str, content_type: str = "application/octet-stream") -> None:
        body = path.read_bytes()
        encoded_name = quote(download_name)
        ascii_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", download_name).strip("._") or "download.7z"
        disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", disposition)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self._write_body(body)

    def _send_static(self, parsed_path: str) -> None:
        relative = unquote(parsed_path.removeprefix("/static/")).replace("\\", "/")
        if not relative or relative.startswith("/") or ".." in Path(relative).parts:
            self._send_html(_html_page("静态资源不存在。"), 404)
            return

        path = _STATIC_ROOT / relative
        if not path.is_file():
            self._send_html(_html_page("静态资源不存在。"), 404)
            return

        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".js":
            content_type = "text/javascript"
        elif path.suffix == ".css":
            content_type = "text/css"

        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self._write_body(body)

    def _send_sticker(self, sticker_id: str) -> None:
        safe_id = Path(unquote(sticker_id or "")).name
        if not safe_id or safe_id != unquote(sticker_id or ""):
            self._send_json({"error": "贴纸不存在。"}, 404)
            return

        path = sticker_library.get_sticker_path(safe_id)
        if path is None:
            self._send_json({"error": "贴纸不存在。"}, 404)
            return

        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "image/gif"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=86400")
        self.end_headers()
        self._write_body(body)

    def _send_pack_archive(self, pack_name: str) -> None:
        archive_path: Path | None = None
        try:
            archive_path = _create_pack_archive(pack_name)
            self._send_download_file(
                archive_path,
                _archive_download_name(pack_name),
                "application/x-7z-compressed",
            )
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except RuntimeError as e:
            self._send_json({"error": str(e)}, 500)
        except Exception as e:
            logger.exception("生成贴纸包 7z 失败: %s", e)
            self._send_json({"error": "生成贴纸包 7z 失败，请检查服务日志。"}, 500)
        finally:
            if archive_path is not None:
                archive_path.unlink(missing_ok=True)

    def _send_voice_file(self, voice_id: str) -> None:
        safe_id = Path(unquote(voice_id or "")).name
        if not safe_id or safe_id != unquote(voice_id or ""):
            self._send_json({"error": "语音不存在。"}, 404)
            return

        path = voice_library.get_voice_path(safe_id)
        if path is None:
            self._send_json({"error": "语音不存在。"}, 404)
            return

        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "audio/mpeg"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=86400")
        self.end_headers()
        self._write_body(body)

    def _send_inbox_item(self, item_id: str) -> None:
        safe_id = Path(unquote(item_id or "")).name
        if not safe_id or safe_id != unquote(item_id or ""):
            self._send_json({"error": "收集项不存在。"}, 404)
            return

        path = sticker_inbox.get_item_path(safe_id)
        if path is None:
            self._send_json({"error": "收集项不存在。"}, 404)
            return

        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/gif")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=86400")
        self.end_headers()
        self._write_body(body)

    def _read_json_body(self) -> dict[str, Any]:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as e:
            raise ValueError("请求格式错误：Content-Length 无效。") from e
        if content_length <= 0:
            raise ValueError("请求内容为空。")
        try:
            data = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError("请求格式错误：JSON 无效。") from e
        if not isinstance(data, dict):
            raise ValueError("请求格式错误：需要 JSON 对象。")
        return data

    # =========================================================================
    # HTTP method entry points (delegate to routing.py)
    # =========================================================================

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        from .routing import dispatch
        dispatch(self, "GET", self.path)

    def do_POST(self) -> None:
        from .routing import dispatch
        dispatch(self, "POST", self.path)

    def do_DELETE(self) -> None:
        from .routing import dispatch
        dispatch(self, "DELETE", self.path)

    # =========================================================================
    # Route handlers — each corresponds to one endpoint in routing.ROUTE_DEFS
    # =========================================================================

    # ---- public (auth=False) ------------------------------------------------

    def _handle_static(self, relative: str) -> None:
        """Serve a static file. ``relative`` is the part after /static/."""
        rel = unquote(relative).replace("\\", "/")
        if not rel or rel.startswith("/") or ".." in Path(rel).parts:
            self._send_html(_html_page("静态资源不存在。"), 404)
            return
        path = _STATIC_ROOT / rel
        if not path.is_file():
            self._send_html(_html_page("静态资源不存在。"), 404)
            return
        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".js":
            content_type = "text/javascript"
        elif path.suffix == ".css":
            content_type = "text/css"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self._write_body(body)

    def _handle_login_page(self) -> None:
        if self._is_authenticated():
            self._redirect("/")
        else:
            self._send_login()

    def _handle_login_action(self) -> None:
        try:
            fields = self._read_form_body()
        except ValueError as e:
            self._send_login(str(e), 400)
            return
        password = fields.get("password", "")
        if _auth_enabled() and hmac.compare_digest(password, _auth_password()):
            max_age = _session_ttl_seconds()
            cookie = f"{_COOKIE_NAME}={_make_session_token()}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax"
            self._redirect("/", cookie)
            return
        if not _auth_enabled():
            self._redirect("/")
            return
        self._send_login("密码不正确。", 401)

    def _handle_logout(self) -> None:
        expired = f"{_COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
        self._redirect("/login", expired)

    # ---- index ---------------------------------------------------------------

    def _handle_index(self) -> None:
        message = self._query_params.get("msg", [""])[0]
        self._send_html(_html_page(message))

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

    # ---- GET API path parameters --------------------------------------------

    def _handle_pack_download(self, name: str) -> None:
        self._send_pack_archive(unquote(name))

    def _handle_pack_detail(self, name: str) -> None:
        try:
            self._send_json(_pack_detail_state(unquote(name)))
        except ValueError as e:
            self._send_json({"error": str(e)}, 404)
        except Exception as e:
            logger.exception("读取贴纸包详情失败: %s", e)
            self._send_json({"error": "读取贴纸包详情失败，请检查服务日志。"}, 500)

    def _handle_configs_detail(self, name: str) -> None:
        try:
            self._send_json(_read_plugin_config(name))
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("读取插件配置失败: %s", e)
            self._send_json({"error": "读取插件配置失败，请检查服务日志。"}, 500)

    def _handle_logs_detail(self, name: str) -> None:
        try:
            max_bytes = self._query_params.get("max_bytes", [_MAX_LOG_TAIL_BYTES])[0]
            self._send_json(_read_log_tail(name, max_bytes))
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("读取日志失败: %s", e)
            self._send_json({"error": "读取日志失败，请检查服务日志。"}, 500)

    def _handle_astrbot_plugin_detail(self, name: str) -> None:
        try:
            self._send_json(astrbot_ops.get_plugin_detail(name))
        except ValueError as e:
            self._send_json({"error": str(e)}, 404)
        except Exception as e:
            logger.exception("读取AstrBot插件详情失败: %s", e)
            self._send_json({"error": "读取插件详情失败，请检查服务日志。"}, 500)

    def _handle_sticker(self, sticker_id: str) -> None:
        self._send_sticker(sticker_id)

    def _handle_upload_status(self, job_id: str) -> None:
        job = _get_upload_job(job_id)
        if job is None:
            self._send_json({"error": "上传任务不存在。"}, 404)
            return
        self._send_json(job)

    def _handle_inbox_image(self, item_id: str) -> None:
        self._send_inbox_item(item_id)

    def _handle_voice_file(self, voice_id: str) -> None:
        self._send_voice_file(voice_id)

    # ---- POST API exact paths -----------------------------------------------

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

    def _handle_access_rules_save(self) -> None:
        try:
            data = self._read_json_body()
            self._send_json(_write_access_rules(data))
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("保存权限规则失败: %s", e)
            self._send_json({"error": "保存权限规则失败，请检查服务日志。"}, 500)

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

    def _handle_voice_keywords_add(self) -> None:
        try:
            data = self._read_json_body()
            voice_id = Path(str(data.get("voice", ""))).name
            keyword = str(data.get("keyword", "")).strip()
            if not voice_id or not voice_library.split_keywords(keyword):
                raise ValueError("语音和关键词都不能为空。")
            voice_library.add_keywords(voice_id, keyword)
            self._send_json(_voice_state())
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("新增语音关键词失败: %s", e)
            self._send_json({"error": "新增语音关键词失败，请检查服务日志。"}, 500)

    def _handle_voices_upload(self) -> None:
        try:
            fields, files = self._parse_multipart_form()
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
            return

        display_name = _safe_voice_name(fields.get("voice_name", ""))
        keyword = fields.get("voice_keyword", "").strip()
        file_infos = [file_info for file_info in files.get("voice_file", []) if file_info.get("filename")]
        if not file_infos:
            self._send_json({"error": "请选择要上传的语音文件。"}, 400)
            return
        if len(file_infos) > MAX_VOICE_UPLOAD_FILES:
            self._send_json({"error": f"一次最多上传 {MAX_VOICE_UPLOAD_FILES} 个语音文件。"}, 400)
            return

        result = _process_voice_uploads(display_name, keyword, file_infos)
        status = 400 if result["status"] == "failed" else 200
        self._send_json(result, status)

    def _handle_keywords_add(self) -> None:
        try:
            data = self._read_json_body()
            pack_name = _safe_pack_name(str(data.get("pack", "")))
            keyword = str(data.get("keyword", "")).strip()
            if not pack_name or not _split_keywords(keyword):
                raise ValueError("贴纸包和关键词都不能为空。")
            _add_trigger_keyword(pack_name, keyword)
            self._send_json(_pack_state())
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("新增贴纸关键词失败: %s", e)
            self._send_json({"error": "新增贴纸关键词失败，请检查服务日志。"}, 500)

    def _handle_pack_stickers_delete(self) -> None:
        try:
            data = self._read_json_body()
            pack_name = _safe_pack_name(str(data.get("pack", "")))
            sticker_ids = [str(sticker_id) for sticker_id in data.get("stickers") or [] if str(sticker_id).strip()]
            result = sticker_library.remove_stickers_from_pack(pack_name, sticker_ids)
            payload = _pack_state()
            payload["result"] = result
            payload["pack_detail"] = sticker_library.get_pack_detail(pack_name)
            self._send_json(payload)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("删除贴纸失败: %s", e)
            self._send_json({"error": "删除贴纸失败，请检查服务日志。"}, 500)

    def _handle_pack_stickers_move(self) -> None:
        try:
            data = self._read_json_body()
            source_pack = _safe_pack_name(str(data.get("source_pack", "")))
            target_pack = _safe_pack_name(str(data.get("target_pack", "")))
            sticker_ids = [str(sticker_id) for sticker_id in data.get("stickers") or [] if str(sticker_id).strip()]
            result = sticker_library.move_stickers_between_packs(source_pack, target_pack, sticker_ids)
            payload = _pack_state()
            payload["result"] = result
            payload["pack_detail"] = sticker_library.get_pack_detail(source_pack)
            self._send_json(payload)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("移动贴纸失败: %s", e)
            self._send_json({"error": "移动贴纸失败，请检查服务日志。"}, 500)

    def _handle_tg_stickers(self) -> None:
        try:
            data = self._read_json_body()
            link = str(data.get("url", "")).strip()
            set_names = extract_sticker_set_names(link)
            if not set_names:
                raise ValueError("请输入有效的 Telegram 贴纸包链接。")

            pack_name = _safe_pack_name(str(data.get("pack", "")))
            target_pack = pack_name or set_names[0]
            keyword = str(data.get("keyword", "")).strip()
            refresh = bool(data.get("refresh", False))
            job = _new_upload_job(target_pack, 0)
            _update_upload_job(
                job["id"],
                status="queued",
                current=set_names[0],
                message=f"已创建 Telegram 导入任务：{set_names[0]}",
            )
            thread = threading.Thread(
                target=_process_tg_sticker_link,
                args=(link, target_pack, keyword, refresh, job["id"]),
                name=f"StickerTgImport-{job['id'][:8]}",
                daemon=True,
            )
            thread.start()
            self._send_json(_get_upload_job(job["id"]) or job, 202)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("创建 Telegram 贴纸导入任务失败: %s", e)
            self._send_json({"error": "创建 Telegram 贴纸导入任务失败，请检查服务日志。"}, 500)

    def _handle_inbox_assign(self) -> None:
        try:
            data = self._read_json_body()
            item_ids = [str(item_id) for item_id in data.get("ids") or [] if str(item_id).strip()]
            pack_name = _safe_pack_name(str(data.get("pack", "")))
            keyword = str(data.get("keyword", "")).strip()
            if not item_ids:
                raise ValueError("请选择要整理的表情。")
            if not pack_name:
                raise ValueError("请选择或输入目标贴纸包。")
            result = sticker_inbox.assign_items(item_ids, pack_name, keyword)
            self._send_json({"result": result, "inbox": _inbox_state(), "state": _pack_state()})
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("整理收集箱贴纸失败: %s", e)
            self._send_json({"error": "整理收集箱贴纸失败，请检查服务日志。"}, 500)

    def _handle_inbox_delete(self) -> None:
        try:
            data = self._read_json_body()
            item_ids = [str(item_id) for item_id in data.get("ids") or [] if str(item_id).strip()]
            if not item_ids:
                raise ValueError("请选择要删除的表情。")
            removed = sticker_inbox.delete_items(item_ids)
            self._send_json({"removed": removed, "inbox": _inbox_state()})
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("删除收集箱贴纸失败: %s", e)
            self._send_json({"error": "删除收集箱贴纸失败，请检查服务日志。"}, 500)

    # ---- POST multipart upload (end-of-chain) -------------------------------

    def _handle_upload_html(self) -> None:
        """HTML form upload (synchronous)."""
        try:
            fields, files = self._parse_multipart_form()
        except ValueError as e:
            self._send_html(_html_page(str(e)), 400)
            return

        existing_pack = _safe_pack_name(fields.get("existing_pack", ""))
        new_pack = _safe_pack_name(fields.get("new_pack", ""))
        keyword = fields.get("keyword", "").strip()
        pack_name = existing_pack or new_pack

        if not pack_name:
            self._send_html(_html_page("请先选择已有贴纸包，或输入新贴纸包名称。"), 400)
            return

        file_infos = [file_info for file_info in files.get("file", []) if file_info.get("filename")]
        if not file_infos:
            self._send_html(_html_page("请选择要上传的文件。"), 400)
            return

        if len(file_infos) > MAX_UPLOAD_FILES:
            self._send_html(_html_page(f"一次最多上传 {MAX_UPLOAD_FILES} 个文件。"), 400)
            return

        result = _process_upload_files(pack_name, keyword, file_infos)
        status = 400 if result["status"] == "failed" else 200
        self._send_html(_html_page(result["message"]), status)

    def _handle_api_uploads(self) -> None:
        """JSON API upload (async background job)."""
        try:
            fields, files = self._parse_multipart_form()
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
            return

        existing_pack = _safe_pack_name(fields.get("existing_pack", ""))
        new_pack = _safe_pack_name(fields.get("new_pack", ""))
        keyword = fields.get("keyword", "").strip()
        pack_name = existing_pack or new_pack

        if not pack_name:
            self._send_json({"error": "请先选择已有贴纸包，或输入新贴纸包名称。"}, 400)
            return

        file_infos = [file_info for file_info in files.get("file", []) if file_info.get("filename")]
        if not file_infos:
            self._send_json({"error": "请选择要上传的文件。"}, 400)
            return

        if len(file_infos) > MAX_UPLOAD_FILES:
            self._send_json({"error": f"一次最多上传 {MAX_UPLOAD_FILES} 个文件。"}, 400)
            return

        job = _new_upload_job(pack_name, len(file_infos))
        thread = threading.Thread(
            target=_process_upload_files,
            args=(pack_name, keyword, file_infos, job["id"]),
            name=f"StickerUpload-{job['id'][:8]}",
            daemon=True,
        )
        thread.start()
        self._send_json(job, 202)

    # ---- DELETE -------------------------------------------------------------

    def _handle_packs_delete(self) -> None:
        pack_name = _safe_pack_name(self._query_params.get("pack", [""])[0])
        if not pack_name:
            self._send_json({"error": "贴纸包不能为空。"}, 400)
            return

        try:
            result = sticker_library.delete_pack(pack_name)
            payload = _pack_state()
            payload["result"] = result
            if not result.get("deleted"):
                payload["error"] = "没有找到这个贴纸包。"
                self._send_json(payload, 404)
                return
            self._send_json(payload)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("删除贴纸包失败: %s", e)
            self._send_json({"error": "删除贴纸包失败，请检查服务日志。"}, 500)

    def _handle_voices_delete(self) -> None:
        voice_id = Path(self._query_params.get("voice", [""])[0]).name
        if not voice_id:
            self._send_json({"error": "语音不能为空。"}, 400)
            return

        try:
            result = voice_library.delete_voice(voice_id)
            payload = _voice_state()
            payload["result"] = result
            if not result.get("deleted"):
                payload["error"] = "没有找到这个语音。"
                self._send_json(payload, 404)
                return
            self._send_json(payload)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("删除语音失败: %s", e)
            self._send_json({"error": "删除语音失败，请检查服务日志。"}, 500)

    def _handle_voice_keywords_delete(self) -> None:
        voice_id = Path(self._query_params.get("voice", [""])[0]).name
        keyword = self._query_params.get("keyword", [""])[0].strip()
        if not voice_id or not keyword:
            self._send_json({"error": "语音和关键词都不能为空。"}, 400)
            return

        removed = voice_library.remove_keyword(voice_id, keyword)
        status = 200 if removed else 404
        payload = _voice_state()
        if not removed:
            payload["error"] = "没有找到这个关键词关联。"
        self._send_json(payload, status)

    def _handle_keywords_delete(self) -> None:
        pack_name = _safe_pack_name(self._query_params.get("pack", [""])[0])
        keyword = self._query_params.get("keyword", [""])[0].strip()
        if not pack_name or not keyword:
            self._send_json({"error": "贴纸包和关键词都不能为空。"}, 400)
            return

        removed = _remove_trigger_keyword(pack_name, keyword)
        status = 200 if removed else 404
        payload = _pack_state()
        if not removed:
            payload["error"] = "没有找到这个关键词关联。"
        self._send_json(payload, status)

    # ---- plugin pages (dynamic, catch-all) ----------------------------------

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

    # =========================================================================
    # Multipart form parser
    # =========================================================================

    def _parse_multipart_form(self) -> tuple[dict[str, str], dict[str, list[dict[str, Any]]]]:
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type.lower():
            raise ValueError("请求格式错误：需要 multipart/form-data。")

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as e:
            raise ValueError("请求格式错误：Content-Length 无效。") from e

        if content_length <= 0:
            raise ValueError("上传内容为空。")

        body = self.rfile.read(content_length)
        raw_message = (
            f"Content-Type: {content_type}\r\n"
            "MIME-Version: 1.0\r\n\r\n"
        ).encode("utf-8") + body
        message = BytesParser(policy=email_policy).parsebytes(raw_message)

        if not message.is_multipart():
            raise ValueError("请求格式错误：未找到 multipart 内容。")

        fields: dict[str, str] = {}
        files: dict[str, list[dict[str, Any]]] = {}

        for part in message.iter_parts():
            disposition = part.get("Content-Disposition", "")
            if "form-data" not in disposition:
                continue

            name = part.get_param("name", header="Content-Disposition")
            if not name:
                continue

            filename = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            if filename:
                files.setdefault(name, []).append({
                    "filename": filename,
                    "content": payload,
                })
            else:
                charset = part.get_content_charset() or "utf-8"
                fields[name] = payload.decode(charset, errors="replace")

        return fields, files
