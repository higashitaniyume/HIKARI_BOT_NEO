from __future__ import annotations

import logging
from typing import Any

from core.config_loader import load_plugin_config

logger = logging.getLogger("HikariBot.PushFramework.Config")

DEFAULT_PUSH_FRAMEWORK_CONFIG: dict[str, Any] = {
    "enabled": True,
    "startup_delay_seconds": 15,
    "check_interval_seconds": 60,
    "send_retry_attempts": 2,
    "send_retry_delay_seconds": 2.0,
    "jobs": [
        {
            "id": "example_daily_text",
            "enabled": False,
            "trigger": "schedule",
            "source": "static_text",
            "time": "09:00",
            "timezone": "Asia/Shanghai",
            "days": [],
            "late_grace_seconds": 7200,
            "dedupe": "daily",
            "targets": {
                "group_ids": [],
                "private_user_ids": [],
            },
            "source_options": {
                "text": "这是一条定时推送示例。",
            },
        }
    ],
}

_first_load_done = False


def get_config() -> dict[str, Any]:
    global _first_load_done
    cfg = load_plugin_config("push_framework", DEFAULT_PUSH_FRAMEWORK_CONFIG)
    if not _first_load_done:
        _first_load_done = True
        jobs = cfg.get("jobs") if isinstance(cfg.get("jobs"), list) else []
        logger.info(
            "推送框架配置加载完成 -> enabled=%s, jobs=%d",
            cfg.get("enabled"),
            len(jobs),
        )
    return cfg
