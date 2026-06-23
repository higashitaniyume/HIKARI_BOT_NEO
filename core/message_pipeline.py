"""
消息处理中心。

所有收到的消息必须先进入此模块统一处理。

处理流程：
    收到消息
      ↓
    进入 core/message_pipeline.py
      ↓
    调用功能分发逻辑
      ↓
    检查是否命中各 handler 的 match()
      ↓
    如果命中，调用 handler 的 handle()

Handler 接口：
    - name: str — handler 名称，用于日志和错误报告
    - match(event, text: str) -> bool — 判断是否应处理此消息
    - handle(bot, event) -> Coroutine — 执行实际处理逻辑
"""

import logging
from typing import Protocol, runtime_checkable

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent

logger = logging.getLogger("HikariBot.MessagePipeline")


@runtime_checkable
class URLHandler(Protocol):
    """功能 Handler 协议。每个 URL 解析功能应实现此接口。"""

    name: str

    async def match(self, event: MessageEvent, text: str) -> bool:
        """判断此消息是否应由此 handler 处理。"""
        ...

    async def handle(self, bot: Bot, event: MessageEvent) -> None:
        """处理消息。"""
        ...


# Handler 注册表
_handlers: list[URLHandler] = []


def register_handler(handler: URLHandler) -> None:
    """
    注册一个消息处理器。

    后续添加 Bilibili、Twitter/X、小红书、YouTube 等解析功能时，
    只需要调用此函数注册新的 handler。

    Args:
        handler: 实现了 URLHandler 协议的对象
    """
    if handler not in _handlers:
        _handlers.append(handler)
        logger.info(f"已注册消息处理器: {handler.name}")


# =========================
# 核心 Pipeline Matcher
# =========================

msg_pipeline = on_message(priority=1, block=False)


@msg_pipeline.handle()
async def _pipeline_handle(bot: Bot, event: MessageEvent):
    """Pipeline 主入口：所有消息都经过此函数。"""

    from core.command_router import is_command_handled
    from core.error_notifier import notify_error_to_superuser, send_user_error

    if is_command_handled(event):
        return

    if not _handlers:
        return

    text = str(event.get_message())

    for handler in _handlers:
        try:
            matched = await handler.match(event, text)
        except Exception as e:
            logger.exception(f"Handler [{handler.name}] match() 异常: {e}")
            continue

        if not matched:
            continue

        logger.info(f"Handler [{handler.name}] 匹配成功，开始处理")
        try:
            await handler.handle(bot, event)
        except Exception as e:
            logger.exception(f"Handler [{handler.name}] 处理异常: {e}")
            try:
                await send_user_error(bot, event)
                await notify_error_to_superuser(bot, event, e, handler.name)
            except Exception as notify_err:
                logger.exception(f"发送错误通知失败: {notify_err}")

        # 当前设计：一个消息可命中多个 handler（不 break）
