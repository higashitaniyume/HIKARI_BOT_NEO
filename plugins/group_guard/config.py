"""群风控（group_guard）配置。

两块能力共用一份配置：
- review：用 AI 审查群成员消息里的极度政治敏感内容，命中后撤回。
- recall_command：引用消息回复「撤回」触发的手动撤回。

群开关走 core.access_control 的黑白名单（permissions）。AI 调用费用由机器人
主人承担，所以默认要求群号显式出现在白名单里才会送审。
"""

from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any

from core.access_control import DEFAULT_ACCESS_RULES, normalize_access_rules
from core.config_loader import load_plugin_config

CONFIG_PATH = Path("BotData/plugin_configs/group_guard.json")

DEFAULT_REVIEW_PROMPT = (
    "你是 QQ 群的内容合规检查器。判断给定消息是否属于「极度政治敏感」内容。\n\n"
    "判定为敏感（risk=true）的情况仅限：\n"
    "1. 明确攻击、辱骂或恶意造谣国家领导人、政党或国家制度；\n"
    "2. 宣扬或煽动分裂国家、颠覆政权、恐怖主义、极端主义；\n"
    "3. 针对重大政治事件的恶意谣言或煽动性内容；\n"
    "4. 明确的种族、民族、宗教仇恨煽动。\n\n"
    "以下一律判定为不敏感（risk=false）：\n"
    "- 普通时政讨论、新闻转述、历史科普；\n"
    "- 游戏、动漫、影视、体育、二次元、梗图和玩笑；\n"
    "- 脏话、对个人的抱怨、日常吐槽、情绪发言；\n"
    "- 含义不明的短语、缩写、表情、乱码。\n\n"
    "判定要保守：只有非常确定时才输出 risk=true，宁可漏判也不要误判。\n\n"
    '只输出 JSON，不要解释、不要 markdown 代码块：\n'
    '{"risk": true, "reason": "不超过30字的中文理由"}\n'
    '无法判断时输出 {"risk": false, "reason": "无法判断"}。'
)

DEFAULT_GROUP_GUARD_CONFIG: dict[str, Any] = {
    "enabled": False,
    "review": {
        "enabled": False,
        # 只审查显式出现在 permissions.whitelist.group 里的群。
        # 关掉它意味着所有通过黑白名单的群都会送审，AI 费用由机器人主人承担。
        "require_group_whitelist": True,
        "skip_handled": True,
        "skip_superuser": True,
        "min_chars": 4,
        "max_chars": 800,
        "temperature": 0.0,
        "max_tokens": 300,
        "timeout_seconds": 20,
        "max_concurrent": 2,
        "prompt": DEFAULT_REVIEW_PROMPT,
    },
    "action": {
        "recall": True,
        "notify_group": False,
        "notify_superuser": True,
    },
    "recall_command": {
        "enabled": True,
        "allow_self_recall": True,
        "allow_other_recall": True,
        # 场景 2 的权限闸门：只有群主 / 群管理 / 超级管理员能撤别人的消息。
        "other_requires_admin": True,
        "reply_on_failure": True,
    },
    "permissions": copy.deepcopy(DEFAULT_ACCESS_RULES),
}


def get_config() -> dict[str, Any]:
    cfg = load_plugin_config("group_guard", DEFAULT_GROUP_GUARD_CONFIG)
    review = cfg.get("review") if isinstance(cfg.get("review"), dict) else {}
    action = cfg.get("action") if isinstance(cfg.get("action"), dict) else {}
    recall = cfg.get("recall_command") if isinstance(cfg.get("recall_command"), dict) else {}
    defaults = DEFAULT_GROUP_GUARD_CONFIG
    return {
        "enabled": _safe_bool(cfg.get("enabled"), False),
        "review": {
            "enabled": _safe_bool(review.get("enabled"), False),
            "require_group_whitelist": _safe_bool(review.get("require_group_whitelist"), True),
            "skip_handled": _safe_bool(review.get("skip_handled"), True),
            "skip_superuser": _safe_bool(review.get("skip_superuser"), True),
            "min_chars": _safe_int(review.get("min_chars"), 4, minimum=1, maximum=200),
            "max_chars": _safe_int(review.get("max_chars"), 800, minimum=50, maximum=4000),
            "temperature": _safe_float(review.get("temperature"), 0.0, minimum=0.0, maximum=2.0),
            "max_tokens": _safe_int(review.get("max_tokens"), 300, minimum=64, maximum=4000),
            "timeout_seconds": _safe_int(review.get("timeout_seconds"), 20, minimum=5, maximum=120),
            "max_concurrent": _safe_int(review.get("max_concurrent"), 2, minimum=1, maximum=16),
            "prompt": str(review.get("prompt") or "").strip() or DEFAULT_REVIEW_PROMPT,
        },
        "action": {
            "recall": _safe_bool(action.get("recall"), True),
            "notify_group": _safe_bool(action.get("notify_group"), False),
            "notify_superuser": _safe_bool(action.get("notify_superuser"), True),
        },
        "recall_command": {
            "enabled": _safe_bool(recall.get("enabled"), True),
            "allow_self_recall": _safe_bool(recall.get("allow_self_recall"), True),
            "allow_other_recall": _safe_bool(recall.get("allow_other_recall"), True),
            "other_requires_admin": _safe_bool(recall.get("other_requires_admin"), True),
            "reply_on_failure": _safe_bool(recall.get("reply_on_failure"), True),
        },
        "permissions": normalize_access_rules(cfg.get("permissions", defaults["permissions"])),
    }


def normalize_config(data: dict[str, Any]) -> dict[str, Any]:
    """把 web 面板提交的配置合并到当前配置上并规范化。"""
    if not isinstance(data, dict):
        raise ValueError("群风控配置必须是 JSON 对象。")

    current = get_config()
    review = data.get("review") if isinstance(data.get("review"), dict) else {}
    action = data.get("action") if isinstance(data.get("action"), dict) else {}
    recall = data.get("recall_command") if isinstance(data.get("recall_command"), dict) else {}
    cur_review = current["review"]
    cur_action = current["action"]
    cur_recall = current["recall_command"]

    merged: dict[str, Any] = {
        "enabled": _safe_bool(data.get("enabled", current["enabled"]), current["enabled"]),
        "review": {
            key: _safe_bool(review.get(key, cur_review[key]), cur_review[key])
            for key in ("enabled", "require_group_whitelist", "skip_handled", "skip_superuser")
        },
        "action": {
            key: _safe_bool(action.get(key, cur_action[key]), cur_action[key])
            for key in ("recall", "notify_group", "notify_superuser")
        },
        "recall_command": {
            key: _safe_bool(recall.get(key, cur_recall[key]), cur_recall[key])
            for key in (
                "enabled",
                "allow_self_recall",
                "allow_other_recall",
                "other_requires_admin",
                "reply_on_failure",
            )
        },
        "permissions": normalize_access_rules(
            data.get("permissions", current["permissions"]),
        ),
    }
    merged["review"].update(
        {
            "min_chars": _safe_int(review.get("min_chars", cur_review["min_chars"]), 4, minimum=1, maximum=200),
            "max_chars": _safe_int(review.get("max_chars", cur_review["max_chars"]), 800, minimum=50, maximum=4000),
            "temperature": _safe_float(
                review.get("temperature", cur_review["temperature"]), 0.0, minimum=0.0, maximum=2.0
            ),
            "max_tokens": _safe_int(review.get("max_tokens", cur_review["max_tokens"]), 300, minimum=64, maximum=4000),
            "timeout_seconds": _safe_int(
                review.get("timeout_seconds", cur_review["timeout_seconds"]), 20, minimum=5, maximum=120
            ),
            "max_concurrent": _safe_int(
                review.get("max_concurrent", cur_review["max_concurrent"]), 2, minimum=1, maximum=16
            ),
            "prompt": str(review.get("prompt", cur_review["prompt"]) or "").strip() or DEFAULT_REVIEW_PROMPT,
        }
    )
    if merged["review"]["min_chars"] > merged["review"]["max_chars"]:
        raise ValueError("最短字数不能大于最长字数。")
    return merged


def save_config(data: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_config(data)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = CONFIG_PATH.with_name(f"{CONFIG_PATH.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, CONFIG_PATH)
    return normalized


def _safe_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    lowered = str(value).strip().casefold()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return default


def _safe_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return min(max(parsed, minimum), maximum)


def _safe_float(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        return default
    return min(max(parsed, minimum), maximum)
