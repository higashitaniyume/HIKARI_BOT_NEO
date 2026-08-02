"""AI Agent 配额模块（对话次数版）。

每笔聊天请求都有两个固定时间窗配额（每日 / 每小时对话次数），额度来源：
- 群聊（group:<id>）扣群共享额度，私聊（user:<id>）扣用户个人额度
- 默认值取自配置 quota.default_user / quota.default_group
- 个别用户 / 群可用 quota.user_overrides / quota.group_overrides 定制（0 = 不限额）
- quota.exempt_user_ids / exempt_group_ids 完全跳过检查与扣费

计次口径：一条用户消息 = 1 次对话（内部工具调用多轮 API 只算 1 次）。
后台任务（如记忆总结）按 quota.count_background 计入但不拦截。

持久化：UserData/aiagent_quota.json，每次记账写盘（文件极小），
线程安全由 threading.RLock 保证（NoneBot 事件循环线程 + admin HTTP 线程共用）。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent

logger = logging.getLogger("HikariBot.AIAgent.Quota")

QUOTA_PATH = Path("UserData/aiagent_quota.json")

_usage: dict[str, dict[str, dict[str, int]]] = {}
_lock = threading.RLock()


# ── 窗口与账本 ────────────────────────────────────────────────────────────


def _window_keys(now: datetime | None = None) -> tuple[str, str]:
    """返回 (日窗口 key, 小时窗口 key)。固定窗口，整点/零点滚动。"""
    now = now or datetime.now()
    return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d-%H")


def _window_resets_at(period: str, now: datetime | None = None) -> str:
    """返回窗口重置时间的可读文本（如「明天 0 点」「16 点整」）。"""
    now = now or datetime.now()
    if period == "day":
        nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return f"{nxt.strftime('%m月%d日')} 0 点"
    nxt = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return f"{nxt.strftime('%H')} 点整"


def scope_for_event(event: MessageEvent) -> str:
    """群聊 → group:<id>；私聊 → user:<id>。"""
    if isinstance(event, GroupMessageEvent):
        return f"group:{event.group_id}"
    return f"user:{event.get_user_id()}"


def _quota_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    quota = cfg.get("quota") if isinstance(cfg.get("quota"), dict) else {}
    return quota


def _sanitize_limit(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return max(parsed, 0)


def _limit_map(quota: dict[str, Any], key: str) -> dict[str, Any]:
    raw = quota.get(key)
    return raw if isinstance(raw, dict) else {}


def _limits_for(cfg: dict[str, Any], scope: str) -> dict[str, int]:
    """取某 scope 的日/时次数限额（0 = 不限额）。优先覆盖，其次默认值。"""
    quota = _quota_cfg(cfg)
    kind = scope.split(":", 1)[0]
    scope_key = scope.split(":", 1)[1]
    defaults: dict[str, Any] = _limit_map(
        quota, "default_group" if kind == "group" else "default_user"
    )
    overrides: dict[str, Any] = _limit_map(
        quota, "group_overrides" if kind == "group" else "user_overrides"
    )
    entry: dict[str, Any] = _limit_map(overrides, scope_key)

    daily = _sanitize_limit(entry.get("daily"), _sanitize_limit(defaults.get("daily"), 0))
    hourly = _sanitize_limit(entry.get("hourly"), _sanitize_limit(defaults.get("hourly"), 0))
    return {"daily": daily, "hourly": hourly}


def _scope_exempt(cfg: dict[str, Any], scope: str) -> bool:
    quota = _quota_cfg(cfg)
    kind, ident = scope.split(":", 1)
    if kind == "group":
        exempt_groups = quota.get("exempt_group_ids")
        if isinstance(exempt_groups, list) and ident in {str(i) for i in exempt_groups}:
            return True
    else:
        exempt_users = quota.get("exempt_user_ids")
        if isinstance(exempt_users, list) and ident in {str(i) for i in exempt_users}:
            return True
    return False


def _is_exempt(cfg: dict[str, Any], event: MessageEvent) -> bool:
    return _scope_exempt(cfg, scope_for_event(event))


# ── 持久化 ────────────────────────────────────────────────────────────────


def _load_usage() -> None:
    global _usage
    if QUOTA_PATH.is_file():
        try:
            data = json.loads(QUOTA_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _usage = data
                return
        except Exception as e:
            logger.warning("[AIAgent] 配额账本读取失败，重新开始: %s", e)
    _usage = {}


def _save_usage() -> None:
    try:
        QUOTA_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = QUOTA_PATH.with_name(f"{QUOTA_PATH.name}.{time.time()}.tmp")
        tmp_path.write_text(json.dumps(_usage, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(QUOTA_PATH)
    except Exception as e:
        logger.warning("[AIAgent] 配额账本写入失败: %s", e)


_load_usage()


def _used(scope: str, period: str, key: str) -> int:
    with _lock:
        bucket = _usage.get(scope, {}).get(period)
        if bucket and bucket.get("k") == key:
            # 兼容旧版 tokens 字段（token 配额时代留下的账本数据）
            return int(bucket.get("count", bucket.get("tokens", 0)))
        return 0


def _record(scope: str, period: str, key: str, count: int) -> None:
    with _lock:
        entry = _usage.setdefault(scope, {}).setdefault(period, {})
        if entry.get("k") != key:
            entry.clear()
            entry["k"] = key
        entry["count"] = int(entry.get("count", 0)) + count
        _save_usage()


# ── 对外接口 ──────────────────────────────────────────────────────────────


def check_quota(cfg: dict[str, Any], event: MessageEvent) -> dict[str, Any] | None:
    """请求发出前的配额检查。

    返回 None 表示放行；返回参数字典表示拦截（用于拼接超限提示消息）。

    配额未启用或 scope 豁免时始终放行。0 = 不限额。
    检查当前窗口（今日 / 本小时）的已用次数是否已达到限额。
    """
    quota = _quota_cfg(cfg)
    if not quota.get("enabled", False):
        return None
    if _is_exempt(cfg, event):
        return None

    scope = scope_for_event(event)
    limits = _limits_for(cfg, scope)
    day_key, hour_key = _window_keys()
    used_day = _used(scope, "day", day_key)
    used_hour = _used(scope, "hour", hour_key)
    now = datetime.now()

    if limits["daily"] > 0 and used_day >= limits["daily"]:
        return {
            "who": "group" if scope.startswith("group:") else "user",
            "period": "day",
            "used": used_day,
            "limit": limits["daily"],
            "resets": _window_resets_at("day", now),
        }
    if limits["hourly"] > 0 and used_hour >= limits["hourly"]:
        return {
            "who": "group" if scope.startswith("group:") else "user",
            "period": "hour",
            "used": used_hour,
            "limit": limits["hourly"],
            "resets": _window_resets_at("hour", now),
        }
    return None


def record_usage(cfg: dict[str, Any], event: MessageEvent, count: int = 1) -> None:
    """按对话次数记账（默认 1 次 = 一条用户消息）。配额未启用或豁免时跳过。"""
    count = int(count)
    if count <= 0:
        return
    quota = _quota_cfg(cfg)
    if not quota.get("enabled", False):
        return
    if _is_exempt(cfg, event):
        return

    scope = scope_for_event(event)
    day_key, hour_key = _window_keys()
    _record(scope, "day", day_key, count)
    _record(scope, "hour", hour_key, count)
    logger.debug(
        "[AIAgent] 配额记账 %s += %d 次（日 %d / 时 %d）",
        scope,
        count,
        _used(scope, "day", day_key),
        _used(scope, "hour", hour_key),
    )


def get_scope_status(cfg: dict[str, Any], scope: str) -> dict[str, Any]:
    """某 scope 的配额状态（admin 页面用，无需事件对象）。"""
    quota = _quota_cfg(cfg)
    exempt = _scope_exempt(cfg, scope)
    limits = _limits_for(cfg, scope)
    day_key, hour_key = _window_keys()
    used_day = _used(scope, "day", day_key)
    used_hour = _used(scope, "hour", hour_key)
    now = datetime.now()

    def block(period: str, limit: int, used: int) -> dict[str, Any]:
        return {
            "limit": limit,
            "used": used,
            "remaining": max(0, limit - used) if limit > 0 else -1,
            "resets_at": _window_resets_at(period, now),
        }

    return {
        "scope": scope,
        "kind": "group" if scope.startswith("group:") else "user",
        "exempt": exempt,
        "enabled": bool(quota.get("enabled", False)),
        "daily": block("day", limits["daily"], used_day),
        "hourly": block("hour", limits["hourly"], used_hour),
    }


def get_quota_status(cfg: dict[str, Any], event: MessageEvent) -> dict[str, Any]:
    """当前事件 scope 的配额状态（「额度」命令用）。"""
    return get_scope_status(cfg, scope_for_event(event))


def get_all_usage() -> dict[str, Any]:
    """返回全部账本原始数据（admin 页面快照用）。"""
    with _lock:
        return dict(_usage)


def reset_scope(scope: str) -> bool:
    """清空某 scope 的用量。成功返回 True，scope 不存在返回 False。"""
    with _lock:
        if scope not in _usage:
            return False
        _usage.pop(scope, None)
        _save_usage()
        return True


def reset_all_usage() -> int:
    """清空全部用量，返回清除的 scope 数量。"""
    with _lock:
        count = len(_usage)
        _usage.clear()
        _save_usage()
        return count


def reset_usage_state() -> None:
    """清空内存账本（测试用）。"""
    with _lock:
        _usage.clear()
