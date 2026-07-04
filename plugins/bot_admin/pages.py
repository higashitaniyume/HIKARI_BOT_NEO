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
  <title>{bot_name} 贴纸管理登录</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
<main class="shell auth-shell">
  <section class="panel auth-panel">
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

