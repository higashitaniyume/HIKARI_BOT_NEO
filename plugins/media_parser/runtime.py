"""Bridge HIKARI config to the upstream media parser runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiohttp

from third_party.astrbot_plugin_media_parser.core.config_manager import ConfigManager
from third_party.astrbot_plugin_media_parser.core.downloader import (
    DownloadManager,
    create_public_only_connector,
)
from third_party.astrbot_plugin_media_parser.core.parser.manager import ParserManager

from .config import normalize_output_modes


@dataclass(slots=True)
class MediaParserRuntime:
    config: dict[str, Any]
    config_manager: ConfigManager
    parser_manager: ParserManager
    download_manager: DownloadManager


def create_runtime(config: dict[str, Any]) -> MediaParserRuntime:
    """Create a fresh upstream runtime from the latest HIKARI JSON config.

    这里会再做一次本地输出模式归一化（幂等）：`parsers.<平台> = 仅视频` 是本地扩展，
    上游把不认识的模式当成「关闭」，所以任何调用方直接传原始配置也不会让平台失效。
    """
    config = normalize_output_modes(dict(config))
    config_manager = ConfigManager(config)
    parser_manager = ParserManager(config_manager.create_parsers())
    download_manager = DownloadManager(
        max_video_size_mb=config_manager.download.max_video_size_mb,
        large_video_threshold_mb=config_manager.download.large_video_threshold_mb,
        cache_dir=config_manager.download.cache_dir,
        cache_dir_available=config_manager.download.cache_dir_available,
        max_concurrent_downloads=config_manager.download.max_concurrent_downloads,
        video_cover_only=config_manager.message.media_display.video_cover_only,
    )
    return MediaParserRuntime(
        config=config,
        config_manager=config_manager,
        parser_manager=parser_manager,
        download_manager=download_manager,
    )


def create_media_session(
    timeout: aiohttp.ClientTimeout,
    proxy_addr: str = "",
) -> aiohttp.ClientSession:
    """Create a session carrying the vendored public-only security connector.

    vendored v7.0.0 下载加固（safe_request）要求媒体下载会话由
    create_public_only_connector 创建，否则抛 UnsafeMediaURLError
    “下载会话未使用公共地址安全连接器”。配置了代理时需把代理地址
    作为受信代理传入，代理本身解析到私网/_fake-ip 时才会被放行。
    """
    proxy_addr = str(proxy_addr or "").strip()
    connector = create_public_only_connector(
        trusted_proxy_urls=[proxy_addr] if proxy_addr else [],
    )
    return aiohttp.ClientSession(timeout=timeout, connector=connector)
