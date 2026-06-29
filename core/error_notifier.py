"""
错误通知模块。

当功能处理失败时：
1. 给 superuser 私发脱敏后的错误信息
2. 脱敏后写入日志
"""

import logging
import re
import traceback
from datetime import datetime
from typing import Any

from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent, MessageEvent

logger = logging.getLogger("HikariBot.ErrorNotifier")

_KEY_VALUE_SECRET_RE = re.compile(
    r"(?i)\b(cookie|token|api[_-]?key|password|passwd|secret)\b\s*[:=]\s*([^\s,;]+)"
)
_AUTH_HEADER_RE = re.compile(r"(?i)(Authorization:\s*)(Bearer|Api-Key)\s+([^\s]+)")
_TG_BOT_TOKEN_RE = re.compile(r"\bbot(\d+):[A-Za-z0-9_-]{20,}")
_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:token|access_token|api_key|key|password|cookie)=)([^&\s]+)"
)
_PIXIV_COOKIE_RE = re.compile(r"(?i)\b(PHPSESSID|cf_clearance|device_token)=[^;\s]+")


def _redact_text(value: Any) -> str:
    """隐藏错误通知和日志里的常见敏感片段。"""
    text = str(value)
    text = _AUTH_HEADER_RE.sub(r"\1\2 [REDACTED]", text)
    text = _KEY_VALUE_SECRET_RE.sub(r"\1=[REDACTED]", text)
    text = _TG_BOT_TOKEN_RE.sub(r"bot\1:[REDACTED]", text)
    text = _QUERY_SECRET_RE.sub(r"\1[REDACTED]", text)
    text = _PIXIV_COOKIE_RE.sub(lambda m: m.group(0).split("=", 1)[0] + "=[REDACTED]", text)
    return text


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
    保留兼容入口，但不再向触发用户或群发送错误提示。

    详细错误只通过 notify_error_to_superuser 私发给超级管理员，
    避免在群聊或私聊窗口暴露失败细节。
    """
    logger.debug("已抑制用户侧错误提示，仅保留超级管理员错误通知")


async def notify_error_to_superuser(
    bot: Bot,
    event: MessageEvent,
    exception: Exception,
    feature_name: str,
) -> None:
    """
    给 superuser 私发脱敏后的错误信息。
    """
    superuser_id = _get_superuser_id()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "===== 错误通知 =====",
        f"时间: {now}",
        f"功能: {feature_name}",
        f"异常类型: {type(exception).__name__}",
        f"异常信息: {_redact_text(exception)}",
    ]

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
        lines.append(f"原始消息: {_redact_text(raw_msg)[:200]}")
    except Exception:
        lines.append("原始消息: 获取失败")

    tb_lines = traceback.format_exception(type(exception), exception, exception.__traceback__)
    tb_text = _redact_text("".join(tb_lines))
    lines.append(f"\n堆栈跟踪:\n{tb_text}")

    full_message = "\n".join(lines)

    logger.error(f"详细错误信息:\n{full_message}")

    if len(full_message) > 2000:
        full_message = full_message[:1950] + "\n... [消息过长已截断，完整脱敏堆栈见日志文件]"

    try:
        await bot.send_private_msg(user_id=int(superuser_id), message=full_message)
        logger.info(f"已向 superuser ({superuser_id}) 发送错误通知")
    except Exception as e:
        logger.error(f"向 superuser 发送错误通知失败: {_redact_text(e)}")
        logger.error(full_message)

