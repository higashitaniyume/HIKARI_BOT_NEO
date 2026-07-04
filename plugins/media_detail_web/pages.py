"""Template loader for media detail web."""

from __future__ import annotations

import html
from pathlib import Path

from core.bot_identity import get_bot_name

_TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "index.html"


def index_page() -> bytes:
    page = _TEMPLATE_PATH.read_text(encoding="utf-8")
    page = page.replace("{{ bot_name }}", html.escape(get_bot_name()))
    return page.encode("utf-8")
