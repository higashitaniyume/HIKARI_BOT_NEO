from __future__ import annotations

import hashlib
import hmac
import time

from .config import get_config

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

