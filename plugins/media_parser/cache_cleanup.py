"""Temporary cache registration for aggregated media parser downloads."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.temp_media_cleaner import (
    DEFAULT_TEMP_MEDIA_TTL_SECONDS,
    register_temp_media_path,
    ttl_seconds_from_config,
)

_MEDIA_PARSER_MARKER = ".astrbot_media_parser"


def media_cache_ttl_seconds(config: dict[str, Any]) -> int:
    download_cfg = config.get("download") if isinstance(config.get("download"), dict) else {}
    return ttl_seconds_from_config(
        download_cfg.get("cache_ttl_seconds"),
        DEFAULT_TEMP_MEDIA_TTL_SECONDS,
    )


def register_metadata_temp_media(metadata: dict[str, Any], *, ttl_seconds: int) -> None:
    """Register downloaded media files or marked media subdirectories for TTL cleanup."""
    seen_dirs: set[Path] = set()
    for raw_path in metadata.get("file_paths") or []:
        if not raw_path:
            continue
        path = Path(str(raw_path)).expanduser().resolve(strict=False)
        if not path.exists():
            continue
        parent = path.parent
        marker = parent / _MEDIA_PARSER_MARKER
        if marker.is_file():
            if parent not in seen_dirs:
                register_temp_media_path(parent, ttl_seconds=ttl_seconds, kind="dir")
                seen_dirs.add(parent)
            continue
        if path.is_file():
            register_temp_media_path(path, ttl_seconds=ttl_seconds, kind="file")
