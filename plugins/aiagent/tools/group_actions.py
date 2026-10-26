"""AI Agent 群管理与群互动工具：禁言 / 踢人 / 戳一戳 / @ 群成员。

设计约束：

- **只作用于当前群**：群号一律取自 `context.event.group_id`，schema 里没有
  `group_id` 参数，模型无法对别的群操作；私聊不下发这些工具。
- **副作用三重把关**（禁言 / 踢人）：
  1. 机器人必须是本群管理员或群主（否则 QQ 侧必然失败，先拦下来给出人话提示）；
  2. 发起人必须是群主 / 管理员，或机器人超级用户（`require_requester_admin`）；
  3. 群聊主动接话（`context.auto`）模式下一律不下发也不执行，避免群友闲聊时被
     “自动执法”。戳一戳 / @ 同样不在主动接话里使用。
- **不可越权**：不能对群主、机器人自己、发起人本人执行禁言 / 踢人；不能禁言其他
  管理员；踢出管理员只有群主（或机器人超级用户）可以。
- **@ 只是渲染标记**：`group_mention` 不发消息，只返回 `[[at:QQ号]]` 标记，由回复
  管线（`plugins/aiagent/mention.py`）替换成真正的 at 段；不能 @ 全体成员或机器人自己。
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from typing import Any

from core.command_router import is_superuser_event

from ..utils import safe_bool, safe_int
from . import group

logger = logging.getLogger("HikariBot.AIAgent.Tools.GroupActions")

GROUP_MUTE = "group_mute"
GROUP_KICK = "group_kick"
GROUP_POKE = "group_poke"
GROUP_MENTION = "group_mention"

TOOL_NAMES = frozenset({GROUP_MUTE, GROUP_KICK, GROUP_POKE, GROUP_MENTION})

DEFAULT_MUTE_MINUTES = 10
DEFAULT_MAX_MUTE_MINUTES = 43200  # 30 天，QQ 单人禁言的上限
MAX_MUTE_MINUTES_LIMIT = 43200
DEFAULT_POKE_PER_HOUR = 20
MAX_POKE_PER_HOUR_LIMIT = 200

_POKE_WINDOW_SECONDS = 3600.0
# 机器人管理员身份缓存：避免每次工具调用都多打一次 get_group_member_info
_BOT_ROLE_TTL_SECONDS = 60.0

_bot_role_cache: dict[str, tuple[str, float]] = {}
_poke_times: dict[str, deque[float]] = {}

# 戳一戳目标别名：我 = 发起人，你 = 机器人自己
_REQUESTER_ALIASES = frozenset({"我", "本人", "me", "myself"})
_BOT_ALIASES = frozenset({"你", "你自己", "机器人", "bot", "self", "self_id"})
_ALL_ALIASES = frozenset({"全体成员", "全体", "all", "everyone", "@全体成员"})

_ADMIN_ROLES = frozenset({"owner", "admin"})


# ── 配置 ──────────────────────────────────────────────────────────────────


def _section(cfg: dict[str, Any], key: str) -> dict[str, Any]:
    tools = cfg.get("tools") if isinstance(cfg, dict) else None
    section = tools.get(key) if isinstance(tools, dict) else None
    return section if isinstance(section, dict) else {}


def mute_config(cfg: dict[str, Any]) -> dict[str, Any]:
    return _section(cfg, "group_mute")


def kick_config(cfg: dict[str, Any]) -> dict[str, Any]:
    return _section(cfg, "group_kick")


def poke_config(cfg: dict[str, Any]) -> dict[str, Any]:
    return _section(cfg, "group_poke")


def mention_config(cfg: dict[str, Any]) -> dict[str, Any]:
    return _section(cfg, "group_mention")


def mute_enabled(cfg: dict[str, Any]) -> bool:
    return safe_bool(mute_config(cfg).get("enabled"), True)


def kick_enabled(cfg: dict[str, Any]) -> bool:
    return safe_bool(kick_config(cfg).get("enabled"), True)


def poke_enabled(cfg: dict[str, Any]) -> bool:
    return safe_bool(poke_config(cfg).get("enabled"), True)


def mention_enabled(cfg: dict[str, Any]) -> bool:
    return safe_bool(mention_config(cfg).get("enabled"), True)


def moderation_enabled(cfg: dict[str, Any]) -> bool:
    return mute_enabled(cfg) or kick_enabled(cfg)


def enabled(cfg: dict[str, Any], name: str) -> bool:
    if name == GROUP_MUTE:
        return mute_enabled(cfg)
    if name == GROUP_KICK:
        return kick_enabled(cfg)
    if name == GROUP_POKE:
        return poke_enabled(cfg)
    if name == GROUP_MENTION:
        return mention_enabled(cfg)
    return False


def can_handle(name: str) -> bool:
    return name in TOOL_NAMES


def allowed_in_context(context: Any) -> bool:
    """这些工具只在「群聊、且不是主动接话」时可用。"""
    if not group.is_group_event(context):
        return False
    return not bool(getattr(context, "auto", False))


def _require_requester_admin(cfg: dict[str, Any], name: str) -> bool:
    section = mute_config(cfg) if name == GROUP_MUTE else kick_config(cfg)
    return safe_bool(section.get("require_requester_admin"), True)


def _max_mute_minutes(cfg: dict[str, Any]) -> int:
    return safe_int(
        mute_config(cfg).get("max_minutes"),
        DEFAULT_MAX_MUTE_MINUTES,
        minimum=1,
        maximum=MAX_MUTE_MINUTES_LIMIT,
    )


def _default_mute_minutes(cfg: dict[str, Any]) -> int:
    return safe_int(
        mute_config(cfg).get("default_minutes"),
        DEFAULT_MUTE_MINUTES,
        minimum=0,
        maximum=_max_mute_minutes(cfg),
    )


def _poke_limit(cfg: dict[str, Any]) -> int:
    return safe_int(
        poke_config(cfg).get("max_per_hour"),
        DEFAULT_POKE_PER_HOUR,
        minimum=0,
        maximum=MAX_POKE_PER_HOUR_LIMIT,
    )


# ── 工具定义 ──────────────────────────────────────────────────────────────


def definitions(cfg: dict[str, Any], context: Any = None) -> list[dict[str, Any]]:
    if context is not None and not allowed_in_context(context):
        return []

    definitions: list[dict[str, Any]] = []

    if mute_enabled(cfg):
        max_minutes = _max_mute_minutes(cfg)
        definitions.append(
            {
                "type": "function",
                "function": {
                    "name": GROUP_MUTE,
                    "description": (
                        "禁言或解除禁言当前群里的一位成员。只有在用户明确要求「禁言/解禁某人」"
                        "时才调用；不能禁言群主、管理员、机器人自己或发起人自己。"
                        "机器人必须是本群管理员，否则会返回失败原因。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "user": {
                                "type": "string",
                                "description": "要禁言的成员：QQ 号，或群名片/昵称（可以是其中一部分）。",
                            },
                            "minutes": {
                                "type": "integer",
                                "description": (
                                    f"禁言分钟数（1-{max_minutes}）；传 0 表示立刻解除禁言。"
                                    f"默认 {_default_mute_minutes(cfg)} 分钟。"
                                ),
                                "minimum": 0,
                                "maximum": max_minutes,
                            },
                        },
                        "required": ["user"],
                        "additionalProperties": False,
                    },
                },
            }
        )

    if kick_enabled(cfg):
        definitions.append(
            {
                "type": "function",
                "function": {
                    "name": GROUP_KICK,
                    "description": (
                        "把一位成员踢出当前群。破坏性操作：只有在用户明确、直接要求「踢掉某人」"
                        "时才调用，不要因为玩笑或你自己的判断就执行。不能踢群主、机器人自己或"
                        "发起人自己；踢管理员只有群主可以。机器人必须是本群管理员。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "user": {
                                "type": "string",
                                "description": "要踢出的成员：QQ 号，或群名片/昵称（可以是其中一部分）。",
                            },
                            "reject_add_request": {
                                "type": "boolean",
                                "description": "是否同时拒绝此人再次加群，默认 false。",
                            },
                        },
                        "required": ["user"],
                        "additionalProperties": False,
                    },
                },
            }
        )

    if poke_enabled(cfg):
        definitions.append(
            {
                "type": "function",
                "function": {
                    "name": GROUP_POKE,
                    "description": (
                        "在当前群里戳一戳某位成员（双击头像）。当用户让你「戳一下某人」"
                        "或想用戳一戳回应时调用。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "user": {
                                "type": "string",
                                "description": (
                                    "要戳的成员：QQ 号、群名片或昵称；"
                                    "「我」表示发起请求的人，「你」表示机器人自己；"
                                    "省略时戳发起请求的人。"
                                ),
                            }
                        },
                        "additionalProperties": False,
                    },
                },
            }
        )

    if mention_enabled(cfg):
        definitions.append(
            {
                "type": "function",
                "function": {
                    "name": GROUP_MENTION,
                    "description": (
                        "拿到一个真正的 @ 标记，用来在回复里 @ 当前群的某位成员。"
                        "调用后把返回的 marker 原样写进回复文本，发送时会变成真正的 @；"
                        "不要自己编造标记，不要 @ 全体成员。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "user": {
                                "type": "string",
                                "description": "要 @ 的成员：QQ 号，或群名片/昵称（可以是其中一部分）。",
                            }
                        },
                        "required": ["user"],
                        "additionalProperties": False,
                    },
                },
            }
        )

    return definitions


# ── 通用 ──────────────────────────────────────────────────────────────────


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _error(message: str, **extra: Any) -> str:
    return _json({"ok": False, "error": message, **extra})


def _event(context: Any) -> Any:
    return getattr(context, "event", None)


def _bot(context: Any) -> Any:
    return getattr(context, "bot", None)


def _group_id(context: Any) -> str:
    return group.group_id_of(context)


def _self_id(context: Any) -> str:
    event = _event(context)
    bot = _bot(context)
    return str(getattr(bot, "self_id", "") or getattr(event, "self_id", "") or "")


def _requester_id(context: Any) -> str:
    event = _event(context)
    try:
        return str(event.get_user_id())
    except Exception:
        return str(getattr(event, "user_id", "") or "")


def _sender_role(context: Any) -> str:
    sender = getattr(_event(context), "sender", None)
    role = str(getattr(sender, "role", "") or "").strip().lower()
    return role if role in {"owner", "admin", "member"} else ""


async def _fetch_role(context: Any, user_id: str) -> str:
    group_id = _group_id(context)
    if not group_id or not user_id:
        return ""
    info = await group.fetch_member_info(_bot(context), group_id, user_id)
    if not isinstance(info, dict):
        return ""
    role = str(info.get("role") or "").strip().lower()
    return role if role in {"owner", "admin", "member"} else ""


async def _requester_role(context: Any) -> str:
    role = _sender_role(context)
    if role:
        return role
    return await _fetch_role(context, _requester_id(context))


async def _bot_role(context: Any) -> str:
    """查机器人在本群的身份（缓存 60 秒）。"""
    group_id = _group_id(context)
    self_id = _self_id(context)
    if not group_id or not self_id:
        return ""
    key = f"{group_id}:{self_id}"
    now = time.monotonic()
    cached = _bot_role_cache.get(key)
    if cached and cached[1] > now:
        return cached[0]
    role = await _fetch_role(context, self_id)
    _bot_role_cache[key] = (role, now + _BOT_ROLE_TTL_SECONDS)
    return role


async def _requester_is_superuser(context: Any) -> bool:
    event = _event(context)
    if event is None:
        return False
    try:
        return bool(is_superuser_event(event))
    except Exception:
        return False


async def _guard_requester(context: Any, cfg: dict[str, Any], name: str) -> str:
    """禁言 / 踢人的发起人权限检查；通过返回空串。"""
    superuser = await _requester_is_superuser(context)
    if _require_requester_admin(cfg, name):
        role = await _requester_role(context)
        if role not in _ADMIN_ROLES and not superuser:
            return "只有群主 / 管理员（或机器人超级用户）才能让机器人执行禁言、踢人。"
    bot_role = await _bot_role(context)
    if bot_role not in _ADMIN_ROLES:
        return "机器人不是本群管理员，无法执行这个操作（请先把机器人设为群管理员）。"
    return ""


async def _resolve_target(
    context: Any, raw_user: Any
) -> tuple[dict[str, Any] | None, str]:
    """把参数解析成本群成员；失败时返回 (None, 错误文案)。"""
    user = str(raw_user or "").strip().lstrip("@").strip()
    if not user:
        return None, "缺少 user 参数（QQ 号或昵称/群名片）"
    member, candidates, error = await group.resolve_member(
        _bot(context), _group_id(context), user
    )
    if member is not None:
        return member, ""
    if candidates:
        names = "、".join(
            str(group.member_view(item).get("display_name") or item.get("user_id"))
            for item in candidates
        )
        return None, f"匹配到多位成员，需要用户确认是哪一位：{names}"
    return None, error or "没有找到这位成员"


async def _target_role(context: Any, member: dict[str, Any]) -> str:
    role = str(member.get("role") or "").strip().lower()
    if role in {"owner", "admin", "member"}:
        return role
    return await _fetch_role(context, str(member.get("user_id") or ""))


async def _guard_target(
    context: Any,
    member: dict[str, Any],
    *,
    action: str,
    requester_role: str,
    superuser: bool,
) -> str:
    """禁言 / 踢人的目标权限检查；通过返回空串。"""
    target_id = str(member.get("user_id") or "")
    if target_id == _self_id(context):
        return "不能对机器人自己执行这个操作。"
    if target_id == _requester_id(context):
        return "不能对自己执行这个操作。"
    target_role = await _target_role(context, member)
    if target_role == "owner":
        return "不能对群主执行这个操作。"
    if target_role == "admin":
        if action == "mute":
            return "不能禁言群管理员。"
        if requester_role != "owner" and not superuser:
            return "只有群主（或机器人超级用户）才能踢出群管理员。"
    return ""


def _member_payload(member: dict[str, Any]) -> dict[str, Any]:
    view = group.member_view(member)
    return {
        "user_id": str(view.get("user_id") or ""),
        "display_name": str(view.get("display_name") or view.get("user_id") or ""),
    }


def _take_poke_slot(group_id: str, limit: int) -> bool:
    """按群做每小时戳一戳限流；limit <= 0 表示不限。"""
    if limit <= 0:
        return True
    now = time.monotonic()
    times = _poke_times.setdefault(group_id, deque())
    while times and now - times[0] >= _POKE_WINDOW_SECONDS:
        times.popleft()
    if len(times) >= limit:
        return False
    times.append(now)
    return True


# ── 工具实现 ──────────────────────────────────────────────────────────────


async def _tool_mute(
    cfg: dict[str, Any], arguments: dict[str, Any], context: Any
) -> str:
    error = await _guard_requester(context, cfg, GROUP_MUTE)
    if error:
        return _error(error)

    member, error = await _resolve_target(context, arguments.get("user"))
    if member is None:
        return _error(error)

    requester_role = await _requester_role(context)
    superuser = await _requester_is_superuser(context)
    error = await _guard_target(
        context,
        member,
        action="mute",
        requester_role=requester_role,
        superuser=superuser,
    )
    if error:
        return _error(error)

    max_minutes = _max_mute_minutes(cfg)
    minutes = safe_int(
        arguments.get("minutes"),
        _default_mute_minutes(cfg),
        minimum=0,
        maximum=max_minutes,
    )
    duration = minutes * 60
    target = _member_payload(member)
    try:
        await _bot(context).call_api(
            "set_group_ban",
            group_id=int(_group_id(context)),
            user_id=int(target["user_id"]),
            duration=duration,
        )
    except Exception as e:
        logger.warning(
            "[AIAgent] 禁言失败 group=%s target=%s minutes=%s: %s",
            _group_id(context),
            target["user_id"],
            minutes,
            e,
        )
        return _error(f"禁言失败：{type(e).__name__}", detail=str(e)[:200])

    action = "unmute" if minutes == 0 else "mute"
    logger.info(
        "[AIAgent] 已%s group=%s target=%s by=%s minutes=%s",
        "解除禁言" if minutes == 0 else "禁言",
        _group_id(context),
        target["user_id"],
        _requester_id(context),
        minutes,
    )
    return _json(
        {
            "ok": True,
            "action": action,
            "user": target,
            "minutes": minutes,
            "duration_seconds": duration,
            "note": "已生效。" if minutes else "已解除禁言。",
        }
    )


async def _tool_kick(
    cfg: dict[str, Any], arguments: dict[str, Any], context: Any
) -> str:
    error = await _guard_requester(context, cfg, GROUP_KICK)
    if error:
        return _error(error)

    member, error = await _resolve_target(context, arguments.get("user"))
    if member is None:
        return _error(error)

    requester_role = await _requester_role(context)
    superuser = await _requester_is_superuser(context)
    error = await _guard_target(
        context,
        member,
        action="kick",
        requester_role=requester_role,
        superuser=superuser,
    )
    if error:
        return _error(error)

    reject_add_request = safe_bool(arguments.get("reject_add_request"), False)
    target = _member_payload(member)
    try:
        await _bot(context).call_api(
            "set_group_kick",
            group_id=int(_group_id(context)),
            user_id=int(target["user_id"]),
            reject_add_request=reject_add_request,
        )
    except Exception as e:
        logger.warning(
            "[AIAgent] 踢人失败 group=%s target=%s: %s",
            _group_id(context),
            target["user_id"],
            e,
        )
        return _error(f"踢人失败：{type(e).__name__}", detail=str(e)[:200])

    logger.info(
        "[AIAgent] 已踢出 group=%s target=%s by=%s reject_add=%s",
        _group_id(context),
        target["user_id"],
        _requester_id(context),
        reject_add_request,
    )
    return _json(
        {
            "ok": True,
            "action": "kick",
            "user": target,
            "reject_add_request": reject_add_request,
            "note": "已踢出该成员。",
        }
    )


async def _tool_poke(
    cfg: dict[str, Any], arguments: dict[str, Any], context: Any
) -> str:
    raw = str(arguments.get("user") or "").strip().lstrip("@").strip()
    lowered = raw.casefold()
    requester_id = _requester_id(context)
    self_id = _self_id(context)

    if not raw:
        target = {"user_id": requester_id, "display_name": "你"}
    elif lowered in _REQUESTER_ALIASES:
        target = {"user_id": requester_id, "display_name": "你"}
    elif lowered in _BOT_ALIASES:
        target = {"user_id": self_id, "display_name": "我"}
    else:
        member, error = await _resolve_target(context, raw)
        if member is None:
            return _error(error)
        target = _member_payload(member)

    if not target["user_id"]:
        return _error("无法确定要戳的成员。")
    if not _take_poke_slot(_group_id(context), _poke_limit(cfg)):
        return _error("戳一戳太频繁了，过一会儿再试。")

    try:
        await _bot(context).call_api(
            "send_poke",
            group_id=int(_group_id(context)),
            user_id=int(target["user_id"]),
        )
    except Exception as e:
        logger.warning(
            "[AIAgent] 戳一戳失败 group=%s target=%s: %s",
            _group_id(context),
            target["user_id"],
            e,
        )
        return _error(f"戳一戳失败：{type(e).__name__}", detail=str(e)[:200])

    logger.info(
        "[AIAgent] 已戳一戳 group=%s target=%s by=%s",
        _group_id(context),
        target["user_id"],
        requester_id,
    )
    return _json({"ok": True, "action": "poke", "user": target, "note": "已经戳了一下。"})


async def _tool_mention(
    cfg: dict[str, Any], arguments: dict[str, Any], context: Any
) -> str:
    raw = str(arguments.get("user") or "").strip().lstrip("@").strip()
    if raw.casefold() in _ALL_ALIASES:
        return _error("不支持 @ 全体成员。")

    member, error = await _resolve_target(context, raw)
    if member is None:
        return _error(error)

    target = _member_payload(member)
    if target["user_id"] == _self_id(context):
        return _error("不能 @ 机器人自己。")

    max_per_reply = safe_int(
        mention_config(cfg).get("max_per_reply"), 3, minimum=1, maximum=10
    )
    marker = f"[[at:{target['user_id']}]]"
    return _json(
        {
            "ok": True,
            "action": "mention",
            "user": target,
            "marker": marker,
            "note": (
                f"把这个 marker 原样写进回复即可 @ 到对方（每轮最多 {max_per_reply} 个）；"
                "不要额外再写 @ 或对方昵称。"
            ),
        }
    )


async def execute(
    name: str,
    cfg: dict[str, Any],
    arguments: dict[str, Any],
    context: Any = None,
) -> str:
    if not enabled(cfg, name):
        return _error(f"{name} is disabled by configuration")
    if not group.is_group_event(context):
        return _error("该工具只能在群聊中使用")
    if getattr(context, "auto", False):
        return _error("主动接话模式下不会执行群管理 / 群互动操作")
    if not _group_id(context):
        return _error("该工具只能在群聊中使用")

    if name == GROUP_MUTE:
        return await _tool_mute(cfg, arguments, context)
    if name == GROUP_KICK:
        return await _tool_kick(cfg, arguments, context)
    if name == GROUP_POKE:
        return await _tool_poke(cfg, arguments, context)
    if name == GROUP_MENTION:
        return await _tool_mention(cfg, arguments, context)
    return _error(f"unknown group action tool: {name}")
