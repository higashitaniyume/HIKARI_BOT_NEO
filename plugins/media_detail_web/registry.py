"""In-memory media token registry for the media detail web page."""

from __future__ import annotations

import mimetypes
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class MediaEntry:
    token: str
    kind: str
    filename: str
    content_type: str
    created_at: float
    expires_at: float
    path: Path | None = None
    remote_url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    source_url: str = ""
    size_bytes: int | None = None
    max_proxy_bytes: int = 0


_entries: dict[str, MediaEntry] = {}
_lock = threading.RLock()


def cleanup_registry(*, max_entries: int, now: float | None = None) -> None:
    """Remove expired entries and trim old records."""
    now = time.time() if now is None else now
    with _lock:
        expired = [token for token, entry in _entries.items() if entry.expires_at <= now]
        for token in expired:
            _entries.pop(token, None)

        overflow = max(0, len(_entries) - max(1, int(max_entries)))
        if overflow <= 0:
            return
        oldest = sorted(_entries.values(), key=lambda item: item.created_at)[:overflow]
        for entry in oldest:
            _entries.pop(entry.token, None)


def get_entry(token: str) -> MediaEntry | None:
    """Get a token entry if it is still valid."""
    with _lock:
        entry = _entries.get(token)
        if entry is None:
            return None
        if entry.expires_at <= time.time():
            _entries.pop(token, None)
            return None
        return entry


def register_file(
    path: Path,
    *,
    kind: str,
    ttl_seconds: int,
    filename: str = "",
    source_url: str = "",
) -> dict[str, Any]:
    """Register a local file and return the public JSON shape."""
    resolved = Path(path).resolve()
    stat = resolved.stat()
    safe_name = _safe_filename(filename or resolved.name)
    content_type = _guess_content_type(safe_name, kind)
    entry = MediaEntry(
        token=uuid.uuid4().hex,
        kind=kind,
        filename=safe_name,
        content_type=content_type,
        created_at=time.time(),
        expires_at=time.time() + max(60, int(ttl_seconds)),
        path=resolved,
        source_url=source_url,
        size_bytes=stat.st_size,
    )
    with _lock:
        _entries[entry.token] = entry
    return _entry_payload(entry, mode="local")


def register_remote(
    url: str,
    *,
    kind: str,
    ttl_seconds: int,
    filename: str = "",
    headers: dict[str, str] | None = None,
    max_proxy_bytes: int = 0,
    source_url: str = "",
) -> dict[str, Any]:
    """Register a parser-produced remote URL for same-origin preview/download."""
    safe_name = _safe_filename(filename or _filename_from_url(url, kind))
    entry = MediaEntry(
        token=uuid.uuid4().hex,
        kind=kind,
        filename=safe_name,
        content_type=_guess_content_type(safe_name, kind),
        created_at=time.time(),
        expires_at=time.time() + max(60, int(ttl_seconds)),
        remote_url=url,
        headers={str(k): str(v) for k, v in (headers or {}).items()},
        source_url=source_url,
        max_proxy_bytes=max(0, int(max_proxy_bytes)),
    )
    with _lock:
        _entries[entry.token] = entry
    return _entry_payload(entry, mode="remote")


def _entry_payload(entry: MediaEntry, *, mode: str) -> dict[str, Any]:
    url = f"/api/media/{entry.token}"
    return {
        "token": entry.token,
        "kind": entry.kind,
        "filename": entry.filename,
        "content_type": entry.content_type,
        "size_bytes": entry.size_bytes,
        "mode": mode,
        "preview_url": url,
        "download_url": f"{url}?download=1",
        "source_url": entry.source_url or entry.remote_url,
    }


def _safe_filename(value: str) -> str:
    value = Path(str(value or "")).name.strip()
    cleaned = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in value)
    cleaned = cleaned.strip(" ._")
    return cleaned or "media"


def _filename_from_url(url: str, kind: str) -> str:
    path = url.split("?", 1)[0].rstrip("/")
    name = Path(path).name
    if name and "." in name:
        return name
    suffix = {
        "image": ".jpg",
        "video": ".mp4",
        "audio": ".mp3",
    }.get(kind, ".bin")
    return f"media{suffix}"


def _guess_content_type(filename: str, kind: str) -> str:
    guessed = mimetypes.guess_type(filename)[0]
    if guessed:
        return guessed
    if kind == "image":
        return "image/jpeg"
    if kind == "video":
        return "video/mp4"
    if kind == "audio":
        return "audio/mpeg"
    return "application/octet-stream"
