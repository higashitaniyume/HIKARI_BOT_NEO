from __future__ import annotations

import html
from email.parser import BytesParser
from email.policy import default as email_policy
import json
import logging
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import get_config

logger = logging.getLogger("HikariBot.StickerWeb")

ALLOWED_EXTS = {".gif", ".jpg", ".jpeg", ".png", ".webp", ".mp4"}
TRIGGER_CONFIG_PATH = Path("BotData/plugin_configs/sticker_trigger.json")
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


def _list_packs() -> list[str]:
    root = _upload_root()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def _register_trigger(pack_name: str, keyword: str = "") -> None:
    trigger_config: dict[str, Any] = {}
    if TRIGGER_CONFIG_PATH.exists():
        try:
            trigger_config = json.loads(TRIGGER_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("读取 sticker_trigger.json 失败，将重建配置: %s", e)

    triggers = trigger_config.setdefault("triggers", {})
    keywords = triggers.get(pack_name, [])
    if not isinstance(keywords, list):
        keywords = [keywords] if keywords else []

    for candidate in (pack_name, keyword.strip()):
        if candidate and candidate not in keywords:
            keywords.append(candidate)

    triggers[pack_name] = keywords
    TRIGGER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    TRIGGER_CONFIG_PATH.write_text(json.dumps(trigger_config, ensure_ascii=False, indent=2), encoding="utf-8")


def _count_media(pack_name: str) -> int:
    folder = _upload_root() / pack_name
    if not folder.is_dir():
        return 0
    return sum(1 for f in folder.iterdir() if f.is_file() and f.suffix.lower() in ALLOWED_EXTS)


def _html_page(message: str = "") -> bytes:
    packs = _list_packs()
    pack_options = "".join(
        f'<option value="{html.escape(pack)}">{html.escape(pack)} ({_count_media(pack)} 个)</option>'
        for pack in packs
    )
    pack_rows = "".join(
        f"<tr><td>{html.escape(pack)}</td><td>{_count_media(pack)}</td></tr>"
        for pack in packs
    ) or '<tr><td colspan="2">暂无贴纸包</td></tr>'
    message_html = f'<div class="notice">{html.escape(message)}</div>' if message else ""

    page = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HIKARI 贴纸上传</title>
  <style>
    body {{ margin: 0; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f6f7f9; color: #1f2328; }}
    main {{ max-width: 860px; margin: 0 auto; padding: 28px 18px 48px; }}
    h1 {{ font-size: 28px; margin: 0 0 8px; }}
    p {{ color: #59636e; line-height: 1.6; }}
    section {{ background: white; border: 1px solid #d8dee4; border-radius: 8px; padding: 18px; margin-top: 18px; }}
    label {{ display: block; font-weight: 600; margin: 14px 0 6px; }}
    input, select, button {{ width: 100%; box-sizing: border-box; font: inherit; padding: 10px 12px; border: 1px solid #c9d1d9; border-radius: 6px; background: white; }}
    button {{ margin-top: 18px; background: #1f6feb; color: white; border-color: #1f6feb; cursor: pointer; font-weight: 700; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
    th, td {{ text-align: left; border-bottom: 1px solid #d8dee4; padding: 10px; }}
    .notice {{ background: #dafbe1; border: 1px solid #2da44e; border-radius: 6px; padding: 10px 12px; margin: 16px 0; }}
    .hint {{ font-size: 14px; color: #6e7781; }}
  </style>
</head>
<body>
<main>
  <h1>HIKARI 贴纸上传</h1>
  <p>上传 GIF、图片、WebP 或 MP4 到已有贴纸包，也可以输入新贴纸包名称自动创建。上传后会自动注册贴纸包触发词。</p>
  {message_html}
  <section>
    <form action="/upload" method="post" enctype="multipart/form-data">
      <label for="existing_pack">上传到已有贴纸包</label>
      <select id="existing_pack" name="existing_pack">
        <option value="">新建贴纸包</option>
        {pack_options}
      </select>

      <label for="new_pack">新贴纸包名称</label>
      <input id="new_pack" name="new_pack" placeholder="选择新建时填写，例如 capoo_gif">

      <label for="keyword">额外触发词</label>
      <input id="keyword" name="keyword" placeholder="可选，例如 猫猫虫">

      <label for="file">选择贴纸文件</label>
      <input id="file" name="file" type="file" accept=".gif,.jpg,.jpeg,.png,.webp,.mp4" required>
      <div class="hint">允许格式：gif / jpg / jpeg / png / webp / mp4</div>

      <button type="submit">上传贴纸</button>
    </form>
  </section>

  <section>
    <h2>当前贴纸包</h2>
    <table>
      <thead><tr><th>贴纸包</th><th>文件数</th></tr></thead>
      <tbody>{pack_rows}</tbody>
    </table>
  </section>
</main>
</body>
</html>"""
    return page.encode("utf-8")


class StickerWebHandler(BaseHTTPRequestHandler):
    server_version = "HikariStickerWeb/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("[StickerWeb] " + fmt, *args)

    def _send_html(self, body: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in {"/", "/index.html"}:
            self._send_html(_html_page("页面不存在。"), 404)
            return
        message = parse_qs(parsed.query).get("msg", [""])[0]
        self._send_html(_html_page(message))

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/upload":
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

        dest_dir = _upload_root() / pack_name
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / filename
        if dest.exists():
            stem = dest.stem
            dest = dest_dir / f"{stem}_{int(time.time())}{suffix}"

        dest.write_bytes(file_info["content"])

        _register_trigger(pack_name, keyword)
        self._send_html(_html_page(f"上传成功：{pack_name}/{dest.name}"))

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
