from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("HikariBot.JMComicAPI.Config")

CONFIG_PATH = Path("BotData/plugin_configs/jmcomic_api.json")

DEFAULT_CONFIG: dict[str, Any] = {
    "allow_group": False,
    # 上传重试：失败后重试次数（不含首次尝试）
    "upload_retry_count": 2,
    # 重试间隔基准（秒），每次递增：base * attempt
    "upload_retry_delay_seconds": 3.0,
    # 单次上传最大等待时间（0 表示不限）
    "upload_timeout_seconds": 60.0,
    # 临时文件缓存 TTL（秒），过期后由 temp_media_cleaner 自动清理
    "cache_ttl_seconds": 600,
}


def _write_config(data: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_config() -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        _write_config(DEFAULT_CONFIG)
        logger.info("已创建 JMComic 插件配置文件: %s", CONFIG_PATH)
        return

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.exception("读取 JMComic 插件配置失败: %s", e)
        return

    changed = False
    for key, value in DEFAULT_CONFIG.items():
        if key not in data:
            data[key] = value
            changed = True
    if changed:
        _write_config(data)
        logger.info("已补全 JMComic 插件配置文件: %s", CONFIG_PATH)


def get_config() -> dict[str, Any]:
    ensure_config()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.exception("读取 JMComic 插件配置失败: %s", e)
        return DEFAULT_CONFIG.copy()

    cfg = DEFAULT_CONFIG.copy()
    cfg.update(data)
    return cfg


ensure_config()
