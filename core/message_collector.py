"""
消息收集模块。

负责将收到的每条消息写入 JSONL 文件：
- 私聊 → UserData/private/<user_id>.jsonl
- 群聊 → UserData/group/<group_id>.jsonl

每条消息一行 JSON，包含完整 raw_event。
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent, MessageEvent, PrivateMessageEvent

logger = logging.getLogger("HikariBot.MessageCollector")

USER_DATA_DIR = Path("UserData")


async def collect_message(bot: Bot, event: MessageEvent) -> None:
    """
    将一条消息写入对应的 JSONL 文件。

    Args:
        bot: NoneBot Bot 实例
        event: OneBot v11 消息事件
    """
    try:
        record = await _build_record(bot, event)
        file_path = _get_jsonl_path(event)

        file_path.parent.mkdir(parents=True, exist_ok=True)

        line = json.dumps(record, ensure_ascii=False)
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

        logger.debug(f"消息已记录 → {file_path.name}")
    except Exception as e:
        logger.exception(f"消息记录失败: {e}")


def _get_jsonl_path(event: MessageEvent) -> Path:
    """根据事件类型确定 JSONL 文件路径。"""
    if isinstance(event, GroupMessageEvent):
        group_id = str(event.group_id)
        return USER_DATA_DIR / "group" / f"{group_id}.jsonl"
    else:
        user_id = str(event.get_user_id())
        return USER_DATA_DIR / "private" / f"{user_id}.jsonl"


async def _build_record(bot: Bot, event: MessageEvent) -> dict[str, Any]:
    """构建单条消息的记录字典。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    record: dict[str, Any] = {
        "time": now,
        "message_id": _safe_get_message_id(event),
        "message_type": "group" if isinstance(event, GroupMessageEvent) else "private",
        "user_id": _safe_get(event, "get_user_id"),
        "nickname": _safe_get_sender_attr(event, "nickname"),
        "raw_message": getattr(event, "raw_message", ""),
        "message": str(event.get_message()) if hasattr(event, "get_message") else "",
        "raw_event": _safe_serialize_event(event),
    }

    # 群聊特有字段
    if isinstance(event, GroupMessageEvent):
        record["group_id"] = str(getattr(event, "group_id", ""))
        record["group_name"] = await _safe_get_group_name(bot, event.group_id)
        record["card"] = _safe_get_sender_attr(event, "card")
    else:
        record["group_id"] = None
        record["group_name"] = None
        record["card"] = None

    return record


def _safe_get_message_id(event: MessageEvent) -> str:
    """安全获取 message_id。"""
    try:
        return str(event.message_id)
    except Exception:
        return ""


def _safe_get(event: MessageEvent, method_name: str) -> str:
    """安全调用 event 的无参方法。"""
    try:
        method = getattr(event, method_name, None)
        if callable(method):
            return str(method())
    except Exception:
        pass

    # 尝试直接获取属性
    try:
        return str(getattr(event, method_name.replace("get_", "").replace("_id", "_id"), ""))
    except Exception:
        return ""


def _safe_get_sender_attr(event: MessageEvent, attr: str) -> Optional[str]:
    """安全获取 sender 的属性。"""
    try:
        sender = getattr(event, "sender", None)
        if sender is not None:
            value = getattr(sender, attr, None)
            if value is not None:
                return str(value)
    except Exception:
        pass
    return None


async def _safe_get_group_name(bot: Bot, group_id: int) -> Optional[str]:
    """安全获取群名称。"""
    try:
        info = await bot.get_group_info(group_id=group_id)
        return str(info.get("group_name", ""))
    except Exception:
        return None


def _safe_serialize_event(event: MessageEvent) -> dict[str, Any]:
    """安全序列化事件对象为字典。"""
    try:
        if hasattr(event, "dict"):
            return event.dict()
    except Exception:
        pass

    try:
        if hasattr(event, "json"):
            import json as _json
            return _json.loads(event.json())
    except Exception:
        pass

    # 最后兜底
    try:
        return {
            "type": type(event).__name__,
            "raw": str(event),
        }
    except Exception:
        return {"error": "unable to serialize event"}
