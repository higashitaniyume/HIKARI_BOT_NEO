from __future__ import annotations

import logging
import threading
from http.server import ThreadingHTTPServer
from typing import Any

from .config import get_config
from .handler import BotAdminHandler

logger = logging.getLogger("HikariBot.BotAdmin")
_server_started = False
_server_lock = threading.Lock()

def _normalize_port(raw_port: Any) -> int:
    try:
        port = int(raw_port)
    except Exception:
        logger.warning("Bot 后台端口无效，使用默认端口 54213: %r", raw_port)
        return 54213

    if not 1 <= port <= 65535:
        logger.warning("Bot 后台端口 %s 超出范围，使用默认端口 54213", port)
        return 54213
    return port


def start_server() -> None:
    global _server_started
    cfg = get_config()
    if not cfg.get("enabled", True):
        logger.info("Bot 后台已关闭")
        return

    with _server_lock:
        if _server_started:
            return
        host = str(cfg.get("host", "0.0.0.0"))
        port = _normalize_port(cfg.get("port", 54213))
        try:
            server = ThreadingHTTPServer((host, port), BotAdminHandler)
        except OSError as e:
            logger.error("Bot 后台启动失败: %s:%s → %s", host, port, e)
            return

        thread = threading.Thread(target=server.serve_forever, name="BotAdminServer", daemon=True)
        thread.start()
        _server_started = True
        logger.info("Bot 后台已启动: http://%s:%s/", host, port)

