"""出站自审查（self_review）配置。

审查对象是机器人**自己即将发出**的文本：普通消息、合并转发节点，以及解析类
插件塞进节点里的全部元信息（标题 / 作者 / 简介 / 热评 / 链接）。命中就拦住这
一次发送。

AI 调用费用由机器人主人承担，所以默认整体关闭；开启后可用 permissions 把审查
限制到指定群 / 私聊会话。
"""

from __future__ import annotations

import copy
from typing import Any

from core.access_control import DEFAULT_ACCESS_RULES
from core.config_loader import load_plugin_config

DEFAULT_SELF_REVIEW_PROMPT = (
    "你是 QQ 机器人的出站内容合规检查器。下面是机器人即将发送的文本"
    "（可能包含它解析到的标题、作者、简介、评论等元信息）。"
    "判断这段内容是否属于「极度政治敏感」内容。\n\n"
    "判定为敏感（risk=true）的情况仅限：\n"
    "1. 明确攻击、辱骂或恶意造谣国家领导人、政党或国家制度；\n"
    "2. 宣扬或煽动分裂国家、颠覆政权、恐怖主义、极端主义；\n"
    "3. 针对重大政治事件的恶意谣言或煽动性内容；\n"
    "4. 明确的种族、民族、宗教仇恨煽动。\n\n"
    "以下一律判定为不敏感（risk=false）：\n"
    "- 普通时政讨论、新闻标题、历史科普；\n"
    "- 游戏、动漫、影视、音乐、体育、二次元、梗图和玩笑；\n"
    "- 脏话、抱怨、吐槽、情绪发言；\n"
    "- 机器人的功能提示、帮助文本、报错信息、链接和文件名。\n\n"
    "判定要保守：只有非常确定时才输出 risk=true，宁可漏判也不要误判——"
    "误判会让机器人无法正常回复。\n\n"
    '只输出 JSON，不要解释、不要 markdown 代码块：\n'
    '{"risk": true, "reason": "不超过30字的中文理由"}\n'
    '无法判断时输出 {"risk": false, "reason": "无法判断"}。'
)

DEFAULT_SELF_REVIEW_CONFIG: dict[str, Any] = {
    "enabled": False,
    "review": {
        "enabled": False,
        "min_chars": 8,
        "max_chars": 1500,
        "temperature": 0.0,
        "max_tokens": 300,
        "timeout_seconds": 12,
        "max_concurrent": 2,
        # 相同文本只问模型一次（帮助文本、固定回复因此几乎不花钱）。
        "cache_size": 512,
        "prompt": DEFAULT_SELF_REVIEW_PROMPT,
    },
    "scope": {
        "group": True,
        "private": True,
        # 合并转发（解析结果基本都走这里）。关掉会漏掉大部分元信息。
        "forward": True,
    },
    "action": {
        "block": True,
        "notify_chat": False,
        "notify_superuser": True,
    },
    # 留空 = 审查所有会话；白名单启用 = 只审查名单内会话；黑名单 = 跳过名单内会话。
    "permissions": copy.deepcopy(DEFAULT_ACCESS_RULES),
}


def get_config() -> dict[str, Any]:
    cfg = load_plugin_config("self_review", DEFAULT_SELF_REVIEW_CONFIG)
    review = cfg.get("review") if isinstance(cfg.get("review"), dict) else {}
    scope = cfg.get("scope") if isinstance(cfg.get("scope"), dict) else {}
    action = cfg.get("action") if isinstance(cfg.get("action"), dict) else {}
    return {
        "enabled": _safe_bool(cfg.get("enabled"), False),
        "review": {
            "enabled": _safe_bool(review.get("enabled"), False),
            "min_chars": _safe_int(review.get("min_chars"), 8, minimum=1, maximum=500),
            "max_chars": _safe_int(review.get("max_chars"), 1500, minimum=50, maximum=6000),
            "temperature": _safe_float(review.get("temperature"), 0.0, minimum=0.0, maximum=2.0),
            "max_tokens": _safe_int(review.get("max_tokens"), 300, minimum=64, maximum=4000),
            "timeout_seconds": _safe_int(review.get("timeout_seconds"), 12, minimum=3, maximum=60),
            "max_concurrent": _safe_int(review.get("max_concurrent"), 2, minimum=1, maximum=16),
            "cache_size": _safe_int(review.get("cache_size"), 512, minimum=0, maximum=8192),
            "prompt": str(review.get("prompt") or "").strip() or DEFAULT_SELF_REVIEW_PROMPT,
        },
        "scope": {
            "group": _safe_bool(scope.get("group"), True),
            "private": _safe_bool(scope.get("private"), True),
            "forward": _safe_bool(scope.get("forward"), True),
        },
        "action": {
            "block": _safe_bool(action.get("block"), True),
            "notify_chat": _safe_bool(action.get("notify_chat"), False),
            "notify_superuser": _safe_bool(action.get("notify_superuser"), True),
        },
        "permissions": cfg.get("permissions")
        if isinstance(cfg.get("permissions"), dict)
        else copy.deepcopy(DEFAULT_ACCESS_RULES),
    }


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
