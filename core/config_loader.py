"""
配置加载模块。

负责：
1. 创建默认配置
2. 读取主配置 (BotData/config.json)
3. 读取插件配置 (BotData/plugin_configs/*.json)
4. 校验配置结构
5. 提供统一配置访问
"""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("HikariBot.ConfigLoader")

# =========================
# 默认配置
# =========================

DEFAULT_MAIN_CONFIG: dict[str, Any] = {
    "bot": {
        "name": "HikariBotNeo",
        "superuser_id": "你的QQ号",
        "log_level": "INFO",
    },
    "napcat": {
        "ws_url": "ws://192.168.31.2:54253/",
        "token": "你的NapCat Token",
        "protocol": "websocket",
    },
    "paths": {
        "bot_data": "BotData",
        "user_data": "UserData",
        "logs": "BotData/logs",
        "plugin_configs": "BotData/plugin_configs",
        "temp_media": "/tmp/hikari_bot",
    },
    "features": {
        "message_collector": True,
        "pixiv_parser": True,
        "cobalt_parser": True,
    },
    "media": {
        "send_path_prefix": "file://",
    },
}

DEFAULT_PIXIV_CONFIG: dict[str, Any] = {
    "cookie": "",
    "auto_parse": True,
    "max_send": 6,
    "max_file_mb": 25,
    "allow_r18": False,
    "cache_dir": "/tmp/hikari_bot",
    "cache_ttl_hours": 24,
    "proxy": "",
    "send_strategy": {
        "prefer_forward_message": True,
        "fallback_to_separate_images": True,
    },
}

DEFAULT_COBALT_CONFIG: dict[str, Any] = {
    "auto_parse": True,
    "cobalt_api": "http://192.168.31.2:54257/",
    "api_timeout": 90,
    "max_send": 6,
    "cache_dir": "/tmp/hikari_bot",
    "api_key": "",
    "send_strategy": {
        "prefer_forward_message": True,
        "fallback_to_separate_media": True,
    },
}

DEFAULT_STICKER_CONFIG: dict[str, Any] = {
    "triggers": {
        "capoo_gif": ["capoo", "猫猫虫"],
    },
}

# =========================
# 路径常量（相对于项目根目录）
# =========================

BOT_DATA = Path("BotData")
CONFIG_FILE = BOT_DATA / "config.json"
PLUGIN_CONFIGS_DIR = BOT_DATA / "plugin_configs"
USER_DATA = Path("UserData")
LOGS_DIR = BOT_DATA / "logs"


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """写入 JSON 文件，自动创建父目录。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"已创建默认配置文件: {path}")


def init_directories(config: dict[str, Any]) -> None:
    """根据配置创建所有需要的目录。"""
    paths = config.get("paths", {})
    dirs_to_create = [
        paths.get("bot_data", "BotData"),
        paths.get("user_data", "UserData"),
        paths.get("logs", "BotData/logs"),
        paths.get("plugin_configs", "BotData/plugin_configs"),
        paths.get("temp_media", "/tmp/hikari_bot"),
        "UserData/private",
        "UserData/group",
    ]
    for d in dirs_to_create:
        try:
            Path(d).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"创建目录失败: {d} — {e}")


def load_main_config() -> dict[str, Any]:
    """
    加载主配置文件 BotData/config.json。
    如果文件不存在，自动创建默认配置。
    如果格式错误，输出明确日志并抛出。
    """
    if not CONFIG_FILE.exists():
        logger.warning(f"主配置文件不存在，正在创建默认配置: {CONFIG_FILE}")
        _write_json(CONFIG_FILE, DEFAULT_MAIN_CONFIG)
        return DEFAULT_MAIN_CONFIG

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        logger.critical(f"主配置文件 JSON 格式错误: {CONFIG_FILE} — {e}")
        raise RuntimeError(f"主配置文件 {CONFIG_FILE} JSON 格式错误: {e}") from e

    # 浅层合并：用户配置里缺失的顶层 key 用默认值补齐
    merged = dict(DEFAULT_MAIN_CONFIG)
    for key in config:
        if key in merged and isinstance(merged[key], dict) and isinstance(config[key], dict):
            merged[key] = {**merged[key], **config[key]}
        else:
            merged[key] = config[key]

    logger.debug(f"主配置加载完成: {CONFIG_FILE}")
    return merged


def load_plugin_config(plugin_name: str, defaults: dict[str, Any]) -> dict[str, Any]:
    """
    加载插件配置文件 BotData/plugin_configs/<plugin_name>.json。
    如果文件不存在，自动创建默认配置。

    Args:
        plugin_name: 插件名称（不含 .json 后缀），如 "pixiv_parser"
        defaults: 默认配置字典

    Returns:
        合并后的配置字典
    """
    config_path = PLUGIN_CONFIGS_DIR / f"{plugin_name}.json"

    if not config_path.exists():
        logger.warning(f"插件配置不存在，正在创建默认配置: {config_path}")
        _write_json(config_path, defaults)
        return dict(defaults)

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            user_config = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"插件配置文件 JSON 格式错误: {config_path} — {e}，将使用默认配置")
        return dict(defaults)

    # 深层合并用户配置到默认配置
    merged = _deep_merge(dict(defaults), user_config)
    logger.debug(f"插件配置加载完成: {config_path}")
    return merged


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并两个字典，override 中的值覆盖 base。"""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def get_config_value(config: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """从嵌套字典中安全获取配置值。"""
    current = config
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return default
    return current
