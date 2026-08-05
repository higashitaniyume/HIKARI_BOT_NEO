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

def _public_collect_page(state: dict) -> bytes:
    """公开的定向收集表情包页面（无需登录）。"""
    name = html.escape(str(state.get("name") or "某人"))
    pack = html.escape(str(state.get("pack") or ""))
    user_id = html.escape(str(state.get("user_id") or ""))
    count = int(state.get("count") or 0)
    bot_name = html.escape(get_bot_name())

    cards: list[str] = []
    for sticker in state.get("stickers") or []:
        sticker_id = str(sticker.get("id") or "")
        if not sticker_id:
            continue
        title = html.escape(str(sticker.get("original_name") or "贴纸"))
        url = f"/collect/{user_id}/sticker/{html.escape(sticker_id)}"
        cards.append(
            f'<a class="collect-card" href="{url}" target="_blank" title="{title}">'
            f'<img src="{url}" alt="贴纸" loading="lazy"></a>'
        )

    if cards:
        grid_html = '<section class="collect-grid">' + "".join(cards) + "</section>"
    else:
        grid_html = '<p class="collect-empty">还没有收集到表情包。</p>'

    page = f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#12152b">
  <title>{name} 的表情包 · {pack}</title>
  <link rel="stylesheet" href="/static/style.css">
  <style>
    .collect-shell {{ max-width: 1080px; margin: 0 auto; padding: 40px 20px 56px; }}
    .collect-head {{ display: flex; align-items: center; gap: 14px; margin-bottom: 6px; }}
    .collect-title {{ margin: 0; font-size: 26px; font-weight: 700; }}
    .collect-sub {{ margin: 0 0 26px; color: var(--muted); }}
    .collect-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 12px; }}
    .collect-card {{ display: block; border: 1px solid var(--border); border-radius: var(--radius-sm);
      overflow: hidden; background: var(--panel); box-shadow: var(--shadow-soft);
      transition: transform .15s ease, box-shadow .15s ease; }}
    .collect-card:hover {{ transform: translateY(-2px); box-shadow: var(--shadow); }}
    .collect-card img {{ display: block; width: 100%; aspect-ratio: 1 / 1; object-fit: contain;
      background: var(--panel-soft); }}
    .collect-empty {{ color: var(--muted); padding: 40px 0; text-align: center; }}
    .collect-foot {{ margin-top: 28px; color: var(--muted); font-size: 12px; text-align: center; }}
  </style>
</head>
<body>
<main class="collect-shell">
  <header>
    <span class="brand-mark" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/></svg>
    </span>
    <p class="eyebrow">{bot_name} Sticker Collection</p>
  </header>
  <h1 class="collect-title">{name} 的表情包</h1>
  <p class="collect-sub">贴纸包「{pack}」 · 共 {count} 张 · 自动收集</p>
  {grid_html}
  <footer class="collect-foot">由 {bot_name} 自动收集，仅供内部使用</footer>
</main>
</body>
</html>'''
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

