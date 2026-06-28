from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("HikariBot.TgSticker.Config")

CONFIG_PATH = Path("BotData/plugin_configs/tg_sticker_parser.json")

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "auto_parse": False,
    "bot_token": "",
    "api_base": "https://api.telegram.org",
    "proxy": "",
    "output_root": "/tmp/hikari_bot/tg_stickers",
    "direct_send_limit": 10,
    "merged_send_limit": 80,
    "max_send_count": 20,
    "send_delay_seconds": 0.5,
    "keep_original": True,
}


def ensure_config() -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(
            json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("已创建 Telegram 贴纸解析配置文件: %s", CONFIG_PATH)


def get_config() -> dict[str, Any]:
    ensure_config()

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.exception("读取 Telegram 贴纸解析配置失败: %s", e)
        return DEFAULT_CONFIG.copy()

    cfg = DEFAULT_CONFIG.copy()
    cfg.update(data)

    token = cfg.get("bot_token", "")
    token_state = "已配置" if token and "替换成" not in token else "未配置"

    logger.debug(
        "Telegram 贴纸解析配置: enabled=%s, auto_parse=%s, token=%s, output_root=%s",
        cfg.get("enabled"),
        cfg.get("auto_parse"),
        token_state,
        cfg.get("output_root"),
    )

    return cfg


get_config()
