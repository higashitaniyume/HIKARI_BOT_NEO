"""HTTP handler for the media detail web page."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from .config import get_config
from .pages import index_page
from .registry import MediaEntry, get_entry
from .service import SUPPORTED_PLATFORM_GROUPS, parse_media_text

logger = logging.getLogger("HikariBot.MediaDetailWeb")


class MediaDetailWebHandler(BaseHTTPRequestHandler):
    server_version = "HikariMediaDetailWeb/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("[MediaDetailWeb] " + fmt, *args)

    def send_response(self, code: int, message: str | None = None) -> None:
        super().send_response(code, message)
        origin = self.headers.get("Origin")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
        else:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, HEAD, OPTIONS, PUT, PATCH")
        request_headers = self.headers.get("Access-Control-Request-Headers")
        if request_headers:
            self.send_header("Access-Control-Allow-Headers", request_headers)
        else:
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Admin-Token, X-Hikari-Admin-Token, Token")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            self._send_html(index_page())
            return
        if parsed.path == "/api/platforms":
            self._send_json({
                "platform_groups": SUPPORTED_PLATFORM_GROUPS,
                "auto_download": bool(get_config().get("auto_download", True)),
            })
            return
        if parsed.path.startswith("/api/media/"):
            token = unquote(parsed.path.removeprefix("/api/media/").strip("/"))
            self._send_media_token(token, parse_qs(parsed.query))
            return
        self._send_html(index_page(), status=404)

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/media/"):
            token = unquote(parsed.path.removeprefix("/api/media/").strip("/"))
            self._send_media_token(token, parse_qs(parsed.query), head_only=True)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/api/parse":
            self._send_json({"error": "页面不存在。"}, 404)
            return

        try:
            payload = self._read_json_body()
            text = str(payload.get("url") or payload.get("text") or "").strip()
            download = payload.get("download") if "download" in payload else None
            timeout = max(30, int(get_config().get("operation_timeout_seconds", 1800)))
            result = asyncio.run(asyncio.wait_for(
                parse_media_text(text, download=None if download is None else bool(download)),
                timeout=timeout,
            ))
            self._send_json(result)
        except asyncio.TimeoutError:
            self._send_json({"error": "解析超时，请稍后重试或降低下载数量。"}, 504)
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            logger.exception("[MediaDetailWeb] parse request failed: %s", e)
            self._send_json({"error": "解析失败，请检查服务日志。"}, 500)

    def _read_json_body(self) -> dict[str, Any]:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as e:
            raise ValueError("请求格式错误：Content-Length 无效。") from e
        limit = max(1024, int(get_config().get("request_body_limit_bytes", 1048576)))
        if content_length <= 0:
            raise ValueError("请求内容为空。")
        if content_length > limit:
            raise ValueError("请求内容过大。")
        try:
            data = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError("请求格式错误：JSON 无效。") from e
        if not isinstance(data, dict):
            raise ValueError("请求格式错误：需要 JSON 对象。")
        return data

    def _send_html(self, body: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self._write_body(body)

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self._write_body(body)

    def _send_media_token(
        self,
        token: str,
        params: dict[str, list[str]],
        *,
        head_only: bool = False,
    ) -> None:
        if not re.fullmatch(r"[a-fA-F0-9]{32}", token or ""):
            self._send_json({"error": "媒体不存在。"}, 404)
            return
        entry = get_entry(token)
        if entry is None:
            self._send_json({"error": "媒体已过期或不存在。"}, 404)
            return
        download = params.get("download", [""])[0] in {"1", "true", "yes"}
        try:
            if entry.path is not None:
                self._send_local_media(entry, download=download, head_only=head_only)
            elif entry.remote_url:
                self._send_remote_media(entry, download=download, head_only=head_only)
            else:
                self._send_json({"error": "媒体不可用。"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            logger.info("[MediaDetailWeb] client disconnected while sending media")
        except Exception as e:
            logger.exception("[MediaDetailWeb] send media failed: %s", e)
            self._send_json({"error": "发送媒体失败，请检查服务日志。"}, 502)

    def _send_local_media(self, entry: MediaEntry, *, download: bool, head_only: bool) -> None:
        assert entry.path is not None
        path = entry.path
        if not path.is_file():
            self._send_json({"error": "媒体文件不存在。"}, 404)
            return

        file_size = path.stat().st_size
        start, end = self._range_for_size(file_size)
        partial = start is not None
        start = 0 if start is None else start
        end = file_size - 1 if end is None else end
        if start >= file_size or end < start:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{file_size}")
            self.end_headers()
            return

        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", entry.content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.send_header("Cache-Control", "private, max-age=3600")
        self.send_header("Content-Disposition", _content_disposition(entry.filename, download))
        self.end_headers()
        if head_only:
            return

        with path.open("rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                self._write_body(chunk)

    def _send_remote_media(self, entry: MediaEntry, *, download: bool, head_only: bool) -> None:
        headers = {
            "User-Agent": "Mozilla/5.0",
            **entry.headers,
        }
        range_header = self.headers.get("Range")
        if range_header:
            headers["Range"] = range_header
        req = urllib.request.Request(entry.remote_url, headers=headers)
        try:
            response = urllib.request.urlopen(req, timeout=60)
        except urllib.error.HTTPError as e:
            self._send_json({"error": f"远程媒体请求失败：HTTP {e.code}"}, 502)
            return

        with response:
            content_length = response.headers.get("Content-Length")
            size = int(content_length) if content_length and content_length.isdigit() else 0
            if entry.max_proxy_bytes and size > entry.max_proxy_bytes:
                self._send_json({"error": "远程媒体超过代理大小限制。"}, 413)
                return

            status = getattr(response, "status", 200)
            self.send_response(206 if status == 206 else 200)
            self.send_header("Content-Type", response.headers.get("Content-Type") or entry.content_type)
            if content_length:
                self.send_header("Content-Length", content_length)
            if response.headers.get("Content-Range"):
                self.send_header("Content-Range", response.headers["Content-Range"])
            self.send_header("Accept-Ranges", response.headers.get("Accept-Ranges") or "bytes")
            self.send_header("Cache-Control", "private, max-age=600")
            self.send_header("Content-Disposition", _content_disposition(entry.filename, download))
            self.end_headers()
            if head_only:
                return

            written = 0
            while True:
                chunk = response.read(1024 * 512)
                if not chunk:
                    break
                written += len(chunk)
                if entry.max_proxy_bytes and written > entry.max_proxy_bytes:
                    break
                self._write_body(chunk)

    def _range_for_size(self, file_size: int) -> tuple[int | None, int | None]:
        header = self.headers.get("Range", "")
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
        if not match:
            return None, None
        start_raw, end_raw = match.groups()
        if not start_raw and not end_raw:
            return None, None
        if start_raw:
            start = int(start_raw)
            end = int(end_raw) if end_raw else file_size - 1
            return start, min(end, file_size - 1)
        suffix = int(end_raw)
        if suffix <= 0:
            return None, None
        return max(0, file_size - suffix), file_size - 1

    def _write_body(self, body: bytes) -> None:
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            raise


def _content_disposition(filename: str, download: bool) -> str:
    disposition = "attachment" if download else "inline"
    encoded_name = quote(filename)
    ascii_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", filename).strip("._") or "media"
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}"
