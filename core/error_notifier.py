"""
错误通知模块。

当功能处理失败时：
1. 给触发用户或群回复简短失败提示
2. 给 superuser 私发详细错误信息
3. 错误写入日志
"""

import logging
import traceback
from datetime import datetime
from typing import Any

from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent, MessageEvent

logger = logging.getLogger("HikariBot.ErrorNotifier")

USER_ERROR_MESSAGE = "解析失败，请稍后再试。"


def _get_superuser_id() -> str:
    """从全局配置获取 superuser ID。"""
    try:
        from core.config_loader import load_main_config
        config = load_main_config()
        return str(config.get("bot", {}).get("superuser_id", "3433559280"))
    except Exception:
        return "3433559280"


async def send_user_error(bot: Bot, event: Event) -> None:
    """
    给触发用户或群回复简短失败提示。
    """
    try:
        from nonebot.adapters.onebot.v11 import Message
        await bot.send(event, Message(USER_ERROR_MESSAGE))
    except Exception as e:
        logger.warning(f"发送用户错误提示失败: {e}")


async def notify_error_to_superuser(
    bot: Bot,
    event: MessageEvent,
    exception: Exception,
    feature_name: str,
) -> None:
    """
    给 superuser 私发详细错误信息。

    详细信息包含：
    - 错误发生时间
    - 来源（私聊/群聊）
    - 用户 QQ
    - 群号（如果有）
    - 原始消息
    - 功能名
    - 异常类型
    - 异常堆栈
    """
    superuser_id = _get_superuser_id()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 构建错误详情
    lines = [
        "===== 错误通知 =====",
        f"时间: {now}",
        f"功能: {feature_name}",
        f"异常类型: {type(exception).__name__}",
        f"异常信息: {exception}",
    ]

    # 事件信息
    try:
        user_id = event.get_user_id()
        lines.append(f"用户 QQ: {user_id}")
    except Exception:
        lines.append("用户 QQ: 获取失败")

    if isinstance(event, GroupMessageEvent):
        lines.append("来源: 群聊")
        try:
            lines.append(f"群号: {event.group_id}")
        except Exception:
            lines.append("群号: 获取失败")
    else:
        lines.append("来源: 私聊")

    try:
        raw_msg = getattr(event, "raw_message", str(event.get_message()))
        lines.append(f"原始消息: {raw_msg[:200]}")
    except Exception:
        lines.append("原始消息: 获取失败")

    # 异常堆栈
    tb_lines = traceback.format_exception(type(exception), exception, exception.__traceback__)
    tb_text = "".join(tb_lines)
    lines.append(f"\n堆栈跟踪:\n{tb_text}")

    full_message = "\n".join(lines)

    # 截断过长的消息（QQ 私聊有长度限制）
    if len(full_message) > 2000:
        full_message = full_message[:1950] + "\n... [消息过长已截断]"

    try:
        await bot.send_private_msg(user_id=int(superuser_id), message=full_message)
        logger.info(f"已向 superuser ({superuser_id}) 发送错误通知")
    except Exception as e:
        logger.error(f"向 superuser 发送错误通知失败: {e}")
        # 最后尝试 log
        logger.error(full_message)
