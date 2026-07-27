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

from .handlers_state import StateHandlerMixin
from .handlers_config import ConfigHandlerMixin
from .handlers_stickers import StickerHandlerMixin
from .handlers_admin import AdminHandlerMixin
from .handlers_plugin import PluginPageHandlerMixin

logger = logging.getLogger("HikariBot.BotAdmin")
_API_TOKEN_HEADERS = ("X-Admin-Token", "X-Hikari-Admin-Token", "Token")


class BotAdminHandler(
    StateHandlerMixin,
    ConfigHandlerMixin,
    StickerHandlerMixin,
    AdminHandlerMixin,
    PluginPageHandlerMixin,
    BaseHTTPRequestHandler,
):
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

    # ---- GET API exact paths -----------------------------------------------
    # (moved to StateHandlerMixin)

    # ---- GET API path parameters (read-only state, kept in base) -------------

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

    # (moved to StickerHandlerMixin: _handle_pack_download, _handle_pack_detail,
    #  _handle_sticker, _handle_upload_status, _handle_inbox_image, _handle_voice_file)

    # ---- POST API exact paths -----------------------------------------------
    # (moved to ConfigHandlerMixin, AdminHandlerMixin, StickerHandlerMixin)

    # ---- POST multipart upload (end-of-chain) -------------------------------
    # (moved to StickerHandlerMixin)

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
