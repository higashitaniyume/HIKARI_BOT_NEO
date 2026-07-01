"""Template loader for media detail web."""

from __future__ import annotations

from pathlib import Path

_TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "index.html"


def index_page() -> bytes:
    return _TEMPLATE_PATH.read_bytes()
