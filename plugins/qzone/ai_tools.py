"""QQ 空间插件暴露给内置 AI Agent 的工具。

只读工具默认可用；发说说是有副作用的写操作，默认关闭：需要同时在
`BotData/plugin_configs/aiagent.json` 的 `tools.plugin_tools` 里打开
`allow_side_effects` 并把 `qzone_publish` 加进 `enabled_names`，且调用者是超管。
"""

from __future__ import annotations

import logging
from typing import Any

from core.ai_tool_registry import AIToolContext, register_ai_tool

from . import service
from .config import VISIBLE_LABELS, VISIBLE_VALUES, get_config

logger = logging.getLogger("HikariBot.QZone.AITools")


@register_ai_tool(
    "qzone_get_feed",
    description=(
        "读取当前 QQ 空间的好友动态（只读）。返回每条动态的昵称、正文、图片数量、"
        "发布时间、tid 和空间链接；可用 tid 让用户自己决定是否点赞或评论。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "count": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": "返回条数，默认 5",
            }
        },
        "additionalProperties": False,
    },
    plugin_name="qzone",
)
async def qzone_get_feed(context: AIToolContext, arguments: dict[str, Any] | None) -> dict[str, Any]:
    cfg = get_config()
    if not cfg["enabled"]:
        return {"error": "QQ 空间插件已关闭"}
    bot = getattr(context, "bot", None)
    if bot is None:
        return {"error": "缺少 NapCat 连接，无法读取空间动态"}

    count = _clamp(arguments.get("count") if isinstance(arguments, dict) else None, 1, 10, 5)
    try:
        items = await service.fetch_feed(bot, count, timeout=cfg["timeout"])
    except (service.QzoneLoginError, service.QzoneActionError) as exc:
        logger.warning("[QZone] AI 读取好友动态失败: %s", exc)
        return {"error": str(exc)[:200]}

    return {
        "ok": True,
        "count": len(items),
        "items": [
            {
                "nickname": item.nickname,
                "uin": item.uin,
                "content": item.content[:200],
                "images": item.image_count,
                "tid": item.tid,
                "url": item.mood_url,
            }
            for item in items
        ],
    }


@register_ai_tool(
    "qzone_publish",
    description=(
        "发布一条 QQ 空间说说（有副作用，默认关闭）。"
        "只有显式开启插件副作用工具后才会提供给模型。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "说说正文（1~1000 字）"},
            "visible": {
                "type": "string",
                "enum": ["public", "friends", "private"],
                "description": "可见范围：public 公开、friends 好友可见、private 仅自己",
            },
        },
        "required": ["content"],
        "additionalProperties": False,
    },
    plugin_name="qzone",
    readonly=False,
    requires_superuser=True,
    enabled_by_default=False,
)
async def qzone_publish(context: AIToolContext, arguments: dict[str, Any] | None) -> dict[str, Any]:
    cfg = get_config()
    if not cfg["enabled"]:
        return {"error": "QQ 空间插件已关闭"}
    bot = getattr(context, "bot", None)
    if bot is None:
        return {"error": "缺少 NapCat 连接，无法发布说说"}

    payload = arguments if isinstance(arguments, dict) else {}
    content = str(payload.get("content") or "").strip()
    if not content:
        return {"error": "content 不能为空"}
    if len(content) > cfg["max_length"]:
        return {"error": f"内容超过 {cfg['max_length']} 字上限"}

    visible_name = str(payload.get("visible") or cfg["default_visible"]).strip().casefold()
    visible = VISIBLE_VALUES.get(visible_name, VISIBLE_VALUES.get(cfg["default_visible"], 1))

    try:
        result = await service.publish(bot, content, visible=visible, timeout=cfg["timeout"])
    except (service.QzoneLoginError, service.QzoneActionError) as exc:
        logger.warning("[QZone] AI 发布说说失败: %s", exc)
        return {"error": str(exc)[:200]}

    return {
        "ok": True,
        "tid": result.get("tid") or "",
        "visible": VISIBLE_LABELS.get(visible, "公开"),
    }


def _clamp(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return min(max(parsed, minimum), maximum)
