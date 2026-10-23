"""群聊自动回复白名单与同群冷却。

自动回复是「没被 @ 也回复」：只有显式列在 `auto_reply.groups` 里的群才会触发，
且同一个群两次回复之间至少间隔 `cooldown_seconds` 秒。白名单不设数量上限。

冷却只记在进程内存里：重启后清零，避免旧状态阻止机器人上线后正常接话。
"""

from __future__ import annotations

import time
from typing import Any

from .utils import safe_bool, safe_int

DEFAULT_COOLDOWN_SECONDS = 5
_MAX_COOLDOWN_SECONDS = 3600

# 上一次开始回复的时间（monotonic），key = 群号
_last_reply_at: dict[str, float] = {}
# 正在生成回复的群：防止一次回复耗时超过冷却时并发触发第二次
_inflight: set[str] = set()


def section(cfg: dict[str, Any]) -> dict[str, Any]:
    value = cfg.get("auto_reply") if isinstance(cfg, dict) else None
    return value if isinstance(value, dict) else {}


def enabled(cfg: dict[str, Any]) -> bool:
    return safe_bool(section(cfg).get("enabled"), False)


def groups(cfg: dict[str, Any]) -> set[str]:
    """白名单群号集合；非数字项直接忽略，不设数量上限。"""
    raw = section(cfg).get("groups")
    if isinstance(raw, str):
        raw = raw.replace("，", ",").split(",")
    if not isinstance(raw, list):
        return set()
    result: set[str] = set()
    for item in raw:
        group_id = str(item).strip()
        if group_id.isdigit():
            result.add(group_id)
    return result


def allowed(cfg: dict[str, Any], group_id: Any) -> bool:
    return enabled(cfg) and str(group_id) in groups(cfg)


def cooldown_seconds(cfg: dict[str, Any]) -> int:
    return safe_int(
        section(cfg).get("cooldown_seconds"),
        DEFAULT_COOLDOWN_SECONDS,
        minimum=0,
        maximum=_MAX_COOLDOWN_SECONDS,
    )


def claim(cfg: dict[str, Any], group_id: Any, *, now: float | None = None) -> bool:
    """尝试占用一次自动回复机会；不在白名单、冷却中或上一次仍在生成时返回 False。"""
    if not allowed(cfg, group_id):
        return False
    key = str(group_id)
    if key in _inflight:
        return False
    cooldown = cooldown_seconds(cfg)
    current = time.monotonic() if now is None else float(now)
    if cooldown > 0 and current - _last_reply_at.get(key, 0.0) < cooldown:
        return False
    _inflight.add(key)
    _last_reply_at[key] = current
    return True


def release(group_id: Any) -> None:
    _inflight.discard(str(group_id))


def note_bot_spoke(cfg: dict[str, Any], group_id: Any, *, now: float | None = None) -> None:
    """记录机器人在该群刚说过话，让接下来的自动回复等待冷却。

    普通 @ 回复也调用这里：刚回答过某人时，不紧接着对下一条普通消息插话。
    """
    if not allowed(cfg, group_id):
        return
    _last_reply_at[str(group_id)] = time.monotonic() if now is None else float(now)


def reset_state() -> None:
    """清空冷却状态（测试用）。"""
    _last_reply_at.clear()
    _inflight.clear()
