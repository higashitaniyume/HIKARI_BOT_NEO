"""Standalone media detail web server."""

from __future__ import annotations

import logging
import threading
from http.server import ThreadingHTTPServer
from typing import Any

from .config import get_config
from .handler import MediaDetailWebHandler

logger = logging.getLogger("HikariBot.MediaDetailWeb")

_server_started = False
_server_lock = threading.Lock()


def _normalize_port(raw_port: Any) -> int:
    try:
        port = int(raw_port)
    except Exception:
        logger.warning("媒体详情 Web 端口无效，使用默认端口 53123: %r", raw_port)
        return 53123
    if not 1 <= port <= 65535:
        logger.warning("媒体详情 Web 端口 %s 超出范围，使用默认端口 53123", port)
        return 53123
    return port


def start_server() -> None:
    """Start the standalone web page if enabled."""
    global _server_started
    cfg = get_config()
    if not cfg.get("enabled", True):
        logger.info("媒体详情 Web 已关闭")
        return

    with _server_lock:
        if _server_started:
            return
        host = str(cfg.get("host") or "0.0.0.0")
        port = _normalize_port(cfg.get("port", 53123))
        try:
            server = ThreadingHTTPServer((host, port), MediaDetailWebHandler)
        except OSError as e:
            logger.error("媒体详情 Web 启动失败: %s:%s -> %s", host, port, e)
            return

        thread = threading.Thread(
            target=server.serve_forever,
            name="MediaDetailWebServer",
            daemon=True,
        )
        thread.start()
        _server_started = True
        logger.info("媒体详情 Web 已启动: http://%s:%s/", host, port)
