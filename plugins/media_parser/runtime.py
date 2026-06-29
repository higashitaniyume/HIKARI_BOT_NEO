"""Bridge HIKARI config to the upstream media parser runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from third_party.astrbot_plugin_media_parser.core.config_manager import ConfigManager
from third_party.astrbot_plugin_media_parser.core.downloader.manager import DownloadManager
from third_party.astrbot_plugin_media_parser.core.parser.manager import ParserManager


@dataclass(slots=True)
class MediaParserRuntime:
    config: dict[str, Any]
    config_manager: ConfigManager
    parser_manager: ParserManager
    download_manager: DownloadManager


def create_runtime(config: dict[str, Any]) -> MediaParserRuntime:
    """Create a fresh upstream runtime from the latest HIKARI JSON config."""
    config_manager = ConfigManager(config)
    parser_manager = ParserManager(config_manager.create_parsers())
    download_manager = DownloadManager(
        max_video_size_mb=config_manager.download.max_video_size_mb,
        large_video_threshold_mb=config_manager.download.large_video_threshold_mb,
        cache_dir=config_manager.download.cache_dir,
        cache_dir_available=config_manager.download.cache_dir_available,
        max_concurrent_downloads=config_manager.download.max_concurrent_downloads,
        video_cover_only=config_manager.message.video_cover_only,
    )
    return MediaParserRuntime(
        config=config,
        config_manager=config_manager,
        parser_manager=parser_manager,
        download_manager=download_manager,
    )
