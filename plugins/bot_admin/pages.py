from __future__ import annotations

import html
import re
from pathlib import Path

from core.bot_identity import get_bot_name

from .constants import _TEMPLATE_PATH

_INCLUDE_RE = re.compile(r"<!--\s*INCLUDE:\s*([A-Za-z0-9_./-]+)\s*-->")


def _template_root() -> Path:
    return _TEMPLATE_PATH.parent.resolve()


def _read_template(relative_path: str | None = None) -> str:
    root = _template_root()
    path = _TEMPLATE_PATH.resolve() if relative_path is None else (root / relative_path).resolve()
    if path != root and root not in path.parents:
        raise ValueError("模板 include 路径无效。")
    return path.read_text(encoding="utf-8")


def _render_includes(template: str, *, depth: int = 0) -> str:
    if depth > 12:
        raise ValueError("模板 include 嵌套过深。")

    def replace(match: re.Match[str]) -> str:
        partial = _read_template(match.group(1))
        return _render_includes(partial, depth=depth + 1)

    return _INCLUDE_RE.sub(replace, template)


def _html_page(message: str = "") -> bytes:
    message_html = f'<div class="notice">{html.escape(message)}</div>' if message else ""
    bot_name = html.escape(get_bot_name())
    template = _render_includes(_read_template())
    page = template.replace("<!-- MESSAGE_HTML -->", message_html)
    page = page.replace("{{ bot_name }}", bot_name)
    return page.encode("utf-8")

def _login_page(message: str = "") -> bytes:
    escaped = html.escape(message)
    bot_name = html.escape(get_bot_name())
    error_html = f'<div class="toast error">{escaped}</div>' if message else ""
    page = f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#12152b">
  <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='1' y2='1'%3E%3Cstop offset='0' stop-color='%236a6ff0'/%3E%3Cstop offset='1' stop-color='%238b5cf6'/%3E%3C/linearGradient%3E%3C/defs%3E%3Crect width='24' height='24' rx='6' fill='url(%23g)'/%3E%3Cpath d='M12 5l1.6 4.4L18 11l-4.4 1.6L12 17l-1.6-4.4L6 11l4.4-1.6z' fill='white'/%3E%3C/svg%3E">
  <title>{bot_name} 贴纸管理登录</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
<main class="shell auth-shell">
  <section class="panel auth-panel">
    <span class="brand-mark" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/></svg>
    </span>
    <p class="eyebrow">{bot_name} Console</p>
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

