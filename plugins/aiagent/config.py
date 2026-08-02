from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any

from .persona import PERSONA_ROOT

logger = logging.getLogger("HikariBot.AIAgent.Config")

CONFIG_PATH = Path("BotData/plugin_configs/aiagent.json")

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "model": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "",
        "model": "deepseek-v4-flash",
        "temperature": 0.7,
        "top_p": 1.0,
        "max_tokens": 8192,
        "timeout_seconds": 120,
        "proxy": "",
        # null（不传 tool_choice）以兼容 DeepSeek V4 思考模式。
        # 可设为 "auto" / "none" / "required"。
        "tool_choice": None,
    },
    "thinking": {
        "enabled": True,
        "reasoning_effort": "high",
    },
    "persona": {
        "skill_path": "BotData/agent_personas/default",
        "max_chars": 12000,
        "include_references": True,
        "reference_max_depth": 1,
        "reference_max_files": 8,
        "reference_max_chars_per_file": 8000,
        "reference_max_total_chars": 24000,
        "fallback_prompt": "你是 {bot_name} 的聊天 AI Agent。请自然、简洁地回复用户。",
    },
    "chat": {
        "max_user_chars": 2000,
        "max_reply_chars": 3500,
        "short_reply_chars": 200,
        "max_history_messages": 10,
        "cooldown_seconds": 3,
        "system_prompt_extra": "",
        "blocked_url_domains": [
            "douyin.com",
            "iesdouyin.com",
            "bilibili.com",
            "b23.tv",
            "xiaohongshu.com",
            "xhslink.com",
            "xiaoheihe.cn",
            "heybox.cn",
            "twitter.com",
            "x.com",
            "t.co",
            "toutiao.com",
            "ixigua.com",
            "kuaishou.com",
            "gifshow.com",
            "weibo.com",
            "weibo.cn",
            "tiktok.com",
            "vm.tiktok.com",
        ],
    },
    "memory": {
        "enabled": True,
        "root": "UserData/aiagent_memory",
        "max_read_chars_per_file": 8000,
        "max_file_chars": 60000,
    },
    "tools": {
        "search": {
            "enabled": True,
            "base_url": "http://searxng-core:8080",
            "timeout_seconds": 30,
            "max_results": 5,
            "safesearch": 1,
            "language": "auto",
            "categories": "general",
        },
        "files": {
            "enabled": True,
            "max_read_chars": 20000,
            "max_write_chars": 20000,
        },
        "plugin_tools": {
            "enabled": True,
            "allow_side_effects": False,
            "enabled_names": [],
            "disabled_names": [],
        },
        "max_tool_rounds": 4,
    },
    # 配额：替代原 permissions 黑白名单。群聊扣群额度，私聊扣用户额度。
    # 额度单位为「对话次数」（一条用户消息 = 1 次），每日 / 每小时各一窗。
    # 所有限额 0 = 不限额；user/group_overrides 可给个别用户/群定制；
    # exempt_* 完全跳过检查与扣费。enabled 默认关闭，在后台「AI 配额」页启用。
    "quota": {
        "enabled": False,
        "default_user": {"daily": 100, "hourly": 10},
        "default_group": {"daily": 300, "hourly": 30},
        "user_overrides": {},
        "group_overrides": {},
        "exempt_user_ids": [],
        "exempt_group_ids": [],
        "count_background": True,
    },
}



def _write_config(data: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def ensure_config() -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    PERSONA_ROOT.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        _write_config(DEFAULT_CONFIG)
        logger.info("已创建 AI Agent 配置文件: %s", CONFIG_PATH)
        return

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(data, dict):
        return

    merged = _deep_merge(DEFAULT_CONFIG, data)
    # 迁移：旧版 permissions 黑白名单已被 quota 取代，从配置中移除。
    # 保留会误导后台「权限」页（该页已不再列出 aiagent）。
    merged.pop("permissions", None)
    if merged != data:
        _write_config(merged)
        logger.info("已补全 AI Agent 配置文件: %s", CONFIG_PATH)


def get_config() -> dict[str, Any]:
    ensure_config()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.exception("读取 AI Agent 配置失败: %s", e)
        return copy.deepcopy(DEFAULT_CONFIG)
    if not isinstance(data, dict):
        return copy.deepcopy(DEFAULT_CONFIG)
    return _deep_merge(DEFAULT_CONFIG, data)


def save_config(data: dict[str, Any]) -> dict[str, Any]:
    cfg = _deep_merge(DEFAULT_CONFIG, data)
    # 迁移：旧版 permissions 黑白名单已被 quota 取代，写入时一并移除。
    cfg.pop("permissions", None)
    _write_config(cfg)
    return copy.deepcopy(cfg)


def _safe_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return min(max(parsed, minimum), maximum)


def safe_persona_max_chars(value: Any) -> int:
    return _safe_int(value, 12000, minimum=1000, maximum=80000)


def _safe_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


