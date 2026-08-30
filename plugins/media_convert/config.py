from __future__ import annotations

import logging
from typing import Any

from core.config_loader import load_plugin_config

logger = logging.getLogger("HikariBot.MediaConvert.Config")

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    # 视频 → GIF 方向的输入大小上限（MB）
    "max_video_mb": 30,
    # 动图 → MP4 方向的下载大小上限（MB）
    "max_image_mb": 50,
    "download_timeout_seconds": 60,
    # 仅作用于动图 → MP4；视频 → GIF 复用 media_transcoder（内部固定 180s）
    "ffmpeg_timeout_seconds": 300,
    "output_ttl_seconds": 600,
    # 视频 → GIF 的画质参数（作用于 media_transcoder 的调色板转换链）
    "gif_fps": 15,
    "gif_width": 0,
    "gif_max_colors": 256,
    "temp_root": "/tmp/hikari_bot/media_convert",
}

_CLAMP_RANGES: tuple[tuple[str, int, int, int], ...] = (
    ("max_video_mb", 1, 500, 30),
    ("max_image_mb", 1, 500, 50),
    ("download_timeout_seconds", 5, 600, 60),
    ("ffmpeg_timeout_seconds", 30, 1800, 300),
    ("output_ttl_seconds", 60, 86400, 600),
    ("gif_fps", 1, 30, 15),
    ("gif_width", 0, 4096, 0),
    ("gif_max_colors", 2, 256, 256),
)


def get_config() -> dict[str, Any]:
    cfg = load_plugin_config("media_convert", DEFAULT_CONFIG)

    for key, lo, hi, default in _CLAMP_RANGES:
        try:
            value = int(cfg.get(key, default))
        except (TypeError, ValueError):
            value = default
        cfg[key] = max(lo, min(hi, value))

    cfg["enabled"] = bool(cfg.get("enabled", True))
    cfg["temp_root"] = str(cfg.get("temp_root") or DEFAULT_CONFIG["temp_root"])
    return cfg
