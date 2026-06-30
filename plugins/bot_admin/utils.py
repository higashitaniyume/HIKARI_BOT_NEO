from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Any

from plugins import voice_library

from .config import get_config

def _safe_pack_name(value: str) -> str:
    value = value.strip()
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", value)
    value = value.strip(" ._")
    return value[:80]


def _safe_voice_name(value: str) -> str:
    return voice_library.safe_voice_name(value)


def _safe_filename(value: str) -> str:
    value = Path(value or "upload").name
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", value)
    value = value.strip(" ._")
    if not value:
        value = f"upload_{int(time.time())}.gif"
    return value[:120]


def _temp_root() -> Path:
    cfg = get_config()
    return Path(str(cfg.get("temp_root", "/tmp/hikari_bot/sticker_uploads")))


def _hash_content(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

