from __future__ import annotations

from typing import Any

from core.config_loader import load_plugin_config

DEFAULT_FRIEND_MANAGER_CONFIG: dict[str, Any] = {
    "enabled": True,
    "auto_approve": True,
    # 验证消息关键词过滤：留空则不检查验证消息，只填关键词则要求验证消息包含该词
    "comment_keyword": "",
    # 白名单：只通过指定 QQ（空列表 = 不限制）
    "allowed_users": [],
    # 黑名单：拒绝指定 QQ
    "blocked_users": [],
    # 通过后是否发送欢迎消息
    "welcome_enabled": True,
    # 添加好友后是否通知超级管理员（优先走 push_framework 的 friend_add 消息源，未配置任务时直发）
    "notify_superuser": True,
}


def get_config() -> dict[str, Any]:
    cfg = load_plugin_config("friend_manager", DEFAULT_FRIEND_MANAGER_CONFIG)
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "auto_approve": bool(cfg.get("auto_approve", True)),
        "comment_keyword": str(cfg.get("comment_keyword", "") or "").strip(),
        "allowed_users": [
            int(u) for u in cfg.get("allowed_users", []) if str(u).strip().isdigit()
        ],
        "blocked_users": [
            int(u) for u in cfg.get("blocked_users", []) if str(u).strip().isdigit()
        ],
        "welcome_enabled": bool(cfg.get("welcome_enabled", True)),
        "notify_superuser": bool(cfg.get("notify_superuser", True)),
    }
