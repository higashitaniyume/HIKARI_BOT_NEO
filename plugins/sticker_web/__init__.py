from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
from email.parser import BytesParser
from email.policy import default as email_policy
from http.cookies import SimpleCookie
import json
import logging
import mimetypes
import re
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from plugins.media_transcoder import STICKER_INPUT_EXTS, TranscodeError, ensure_sticker_gif

from .config import get_config

logger = logging.getLogger("HikariBot.StickerWeb")

ALLOWED_EXTS = STICKER_INPUT_EXTS
OUTPUT_EXTS = {".gif"}
TRIGGER_CONFIG_PATH = Path("BotData/plugin_configs/sticker_trigger.json")
_TEMPLATE_PATH = Path(__file__).parent / "templates" / "index.html"
_STATIC_ROOT = Path(__file__).parent / "static"
_COOKIE_NAME = "hikari_sticker_session"
_server_started = False
_server_lock = threading.Lock()


def _safe_pack_name(value: str) -> str:
    value = value.strip()
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", value)
    value = value.strip(" ._")
    return value[:80]


def _safe_filename(value: str) -> str:
    value = Path(value or "upload").name
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", value)
    value = value.strip(" ._")
    if not value:
        value = f"upload_{int(time.time())}.gif"
    return value[:120]


def _upload_root() -> Path:
    cfg = get_config()
    return Path(str(cfg.get("upload_root", "BotData/Gifs")))


def _temp_root() -> Path:
    cfg = get_config()
    return Path(str(cfg.get("temp_root", "/tmp/hikari_bot/sticker_uploads")))


def _hash_content(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _list_packs() -> list[str]:
    root = _upload_root()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def _read_trigger_config() -> dict[str, Any]:
    trigger_config: dict[str, Any] = {}
    if TRIGGER_CONFIG_PATH.exists():
        try:
            trigger_config = json.loads(TRIGGER_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("读取 sticker_trigger.json 失败，将重建配置: %s", e)
    trigger_config.setdefault("triggers", {})
    return trigger_config


def _write_trigger_config(trigger_config: dict[str, Any]) -> None:
    TRIGGER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    TRIGGER_CONFIG_PATH.write_text(json.dumps(trigger_config, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_keywords(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_keywords = value
    elif value:
        raw_keywords = [value]
    else:
        raw_keywords = []

    keywords: list[str] = []
    for raw_keyword in raw_keywords:
        keyword = str(raw_keyword).strip()
        if keyword and keyword not in keywords:
            keywords.append(keyword)
    return keywords


def _register_trigger(pack_name: str, keyword: str = "") -> None:
    trigger_config = _read_trigger_config()
    triggers = trigger_config.setdefault("triggers", {})
    keywords = _normalize_keywords(triggers.get(pack_name, []))

    for candidate in (pack_name, keyword.strip()):
        if candidate and candidate not in keywords:
            keywords.append(candidate)

    triggers[pack_name] = keywords
    _write_trigger_config(trigger_config)


def _add_trigger_keyword(pack_name: str, keyword: str) -> None:
    trigger_config = _read_trigger_config()
    triggers = trigger_config.setdefault("triggers", {})
    keywords = _normalize_keywords(triggers.get(pack_name, []))
    if keyword not in keywords:
        keywords.append(keyword)
    triggers[pack_name] = keywords
    _write_trigger_config(trigger_config)


def _remove_trigger_keyword(pack_name: str, keyword: str) -> bool:
    trigger_config = _read_trigger_config()
    triggers = trigger_config.setdefault("triggers", {})
    keywords = _normalize_keywords(triggers.get(pack_name, []))
    next_keywords = [kw for kw in keywords if kw != keyword]
    if len(next_keywords) == len(keywords):
        return False
    triggers[pack_name] = next_keywords
    _write_trigger_config(trigger_config)
    return True


def _count_media(pack_name: str) -> int:
    folder = _upload_root() / pack_name
    if not folder.is_dir():
        return 0
    return sum(1 for f in folder.iterdir() if f.is_file() and f.suffix.lower() in OUTPUT_EXTS)


def _html_page(message: str = "") -> bytes:
    message_html = f'<div class="notice">{html.escape(message)}</div>' if message else ""
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    page = template.replace("<!-- MESSAGE_HTML -->", message_html)
    return page.encode("utf-8")


def _pack_state() -> dict[str, Any]:
    trigger_config = _read_trigger_config()
    triggers = trigger_config.setdefault("triggers", {})
    known_packs = set(_list_packs()) | {str(pack) for pack in triggers}
    packs: list[dict[str, Any]] = []
    keyword_map: dict[str, list[str]] = {}

    for pack_name in sorted(known_packs):
        keywords = _normalize_keywords(triggers.get(pack_name, []))
        packs.append({
            "name": pack_name,
            "count": _count_media(pack_name),
            "keywords": keywords,
        })
        for keyword in keywords:
            keyword_map.setdefault(keyword, []).append(pack_name)

    keywords = [
        {"keyword": keyword, "packs": sorted(pack_names)}
        for keyword, pack_names in sorted(keyword_map.items(), key=lambda item: item[0])
    ]
    return {"packs": packs, "keywords": keywords}


def _json_bytes(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def _auth_password() -> str:
    return str(get_config().get("password", "")).strip()


def _auth_enabled() -> bool:
    return bool(_auth_password())


def _session_ttl_seconds() -> int:
    try:
        ttl = int(get_config().get("session_ttl_seconds", 604800))
    except Exception:
        return 604800
    return max(60, ttl)


def _make_session_token(timestamp: int | None = None) -> str:
    timestamp = timestamp or int(time.time())
    payload = str(timestamp)
    signature = hmac.new(_auth_password().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def _valid_session_token(token: str) -> bool:
    if not _auth_enabled():
        return True
    try:
        raw_timestamp, signature = token.split(".", 1)
        timestamp = int(raw_timestamp)
    except Exception:
        return False

    if timestamp <= 0 or time.time() - timestamp > _session_ttl_seconds():
        return False

    expected = hmac.new(_auth_password().encode("utf-8"), raw_timestamp.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def _login_page(message: str = "") -> bytes:
    escaped = html.escape(message)
    error_html = f'<div class="toast error">{escaped}</div>' if message else ""
    page = f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HIKARI 贴纸管理登录</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
<main class="shell auth-shell">
  <section class="panel auth-panel">
    <p class="eyebrow">HIKARI Sticker Web</p>
    <h1>输入管理密码</h1>
    {error_html}
    <form action="/login" method="post" class="login-form">
      <label>
        <span>密码</span>
        <input name="password" type="password" autocomplete="current-password" autofocus required>
      </label>
      <button type="submit" class="primary">登录</button>
    </form>
  </section>
</main>
</body>
</html>'''
    return page.encode("utf-8")


class StickerWebHandler(BaseHTTPRequestHandler):
    server_version = "HikariStickerWeb/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("[StickerWeb] " + fmt, *args)

    def _send_html(self, body: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str, cookie: str | None = None) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def _is_authenticated(self) -> bool:
        if not _auth_enabled():
            return True
        cookie_header = self.headers.get("Cookie", "")
        cookie = SimpleCookie(cookie_header)
        morsel = cookie.get(_COOKIE_NAME)
        return bool(morsel and _valid_session_token(morsel.value))

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
        self.wfile.write(body)

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
        self.wfile.write(body)

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

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/static/"):
            self._send_static(parsed.path)
            return
        if parsed.path == "/login":
            if self._is_authenticated():
                self._redirect("/")
            else:
                self._send_login()
            return
        if parsed.path == "/logout":
            expired = f"{_COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
            self._redirect("/login", expired)
            return
        if parsed.path == "/api/state":
            if not self._is_authenticated():
                self._unauthorized_json()
                return
            self._send_json(_pack_state())
            return
        if parsed.path not in {"/", "/index.html"}:
            self._send_html(_html_page("页面不存在。"), 404)
            return
        if not self._is_authenticated():
            self._send_login()
            return
        message = parse_qs(parsed.query).get("msg", [""])[0]
        self._send_html(_html_page(message))

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/login":
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
            return

        if not self._is_authenticated():
            if path.startswith("/api/"):
                self._unauthorized_json()
            else:
                self._send_login("请先登录。", 401)
            return
        if path == "/api/keywords":
            try:
                data = self._read_json_body()
                pack_name = _safe_pack_name(str(data.get("pack", "")))
                keyword = str(data.get("keyword", "")).strip()
                if not pack_name or not keyword:
                    raise ValueError("贴纸包和关键词都不能为空。")
                _add_trigger_keyword(pack_name, keyword)
                self._send_json(_pack_state())
            except ValueError as e:
                self._send_json({"error": str(e)}, 400)
            except Exception as e:
                logger.exception("新增贴纸关键词失败: %s", e)
                self._send_json({"error": "新增贴纸关键词失败，请检查服务日志。"}, 500)
            return

        if path != "/upload":
            self._send_html(_html_page("页面不存在。"), 404)
            return

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

        file_info = files.get("file")
        if file_info is None or not file_info["filename"]:
            self._send_html(_html_page("请选择要上传的文件。"), 400)
            return

        filename = _safe_filename(file_info["filename"])
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_EXTS:
            self._send_html(_html_page(f"不支持的文件格式：{suffix}"), 400)
            return

        content = file_info["content"]
        content_hash = _hash_content(content)
        dest_dir = _upload_root() / pack_name
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{content_hash[:16]}.gif"

        _register_trigger(pack_name, keyword)
        if dest.exists() and dest.stat().st_size > 0:
            self._send_html(_html_page(f"贴纸已存在，已复用：{pack_name}/{dest.name}"))
            return

        temp_dir = _temp_root()
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=suffix,
                prefix=f"{content_hash[:16]}_",
                dir=temp_dir,
                delete=False,
            ) as temp_file:
                temp_file.write(content)
                temp_path = Path(temp_file.name)

            asyncio.run(ensure_sticker_gif(temp_path, dest))
        except TranscodeError as e:
            if dest.exists():
                dest.unlink(missing_ok=True)
            self._send_html(_html_page(f"贴纸转 GIF 失败：{e}"), 400)
            return
        except Exception as e:
            logger.exception("贴纸上传处理失败: %s", e)
            if dest.exists():
                dest.unlink(missing_ok=True)
            self._send_html(_html_page("贴纸上传处理失败，请检查服务日志。"), 500)
            return
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

        self._send_html(_html_page(f"上传成功：{pack_name}/{dest.name}"))

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if not self._is_authenticated():
            self._unauthorized_json()
            return
        if parsed.path != "/api/keywords":
            self._send_json({"error": "页面不存在。"}, 404)
            return

        params = parse_qs(parsed.query)
        pack_name = _safe_pack_name(params.get("pack", [""])[0])
        keyword = params.get("keyword", [""])[0].strip()
        if not pack_name or not keyword:
            self._send_json({"error": "贴纸包和关键词都不能为空。"}, 400)
            return

        removed = _remove_trigger_keyword(pack_name, keyword)
        status = 200 if removed else 404
        payload = _pack_state()
        if not removed:
            payload["error"] = "没有找到这个关键词关联。"
        self._send_json(payload, status)

    def _parse_multipart_form(self) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
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
        files: dict[str, dict[str, Any]] = {}

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
                files[name] = {
                    "filename": filename,
                    "content": payload,
                }
            else:
                charset = part.get_content_charset() or "utf-8"
                fields[name] = payload.decode(charset, errors="replace")

        return fields, files


def _normalize_port(raw_port: Any) -> int:
    try:
        port = int(raw_port)
    except Exception:
        logger.warning("贴纸上传页面端口无效，使用默认端口 54213: %r", raw_port)
        return 54213

    if not 1 <= port <= 65535:
        logger.warning("贴纸上传页面端口 %s 超出范围，使用默认端口 54213", port)
        return 54213
    return port


def start_server() -> None:
    global _server_started
    cfg = get_config()
    if not cfg.get("enabled", True):
        logger.info("贴纸上传页面已关闭")
        return

    with _server_lock:
        if _server_started:
            return
        host = str(cfg.get("host", "0.0.0.0"))
        port = _normalize_port(cfg.get("port", 54213))
        try:
            server = ThreadingHTTPServer((host, port), StickerWebHandler)
        except OSError as e:
            logger.error("贴纸上传页面启动失败: %s:%s → %s", host, port, e)
            return

        thread = threading.Thread(target=server.serve_forever, name="StickerWebServer", daemon=True)
        thread.start()
        _server_started = True
        logger.info("贴纸上传页面已启动: http://%s:%s/", host, port)


start_server()


