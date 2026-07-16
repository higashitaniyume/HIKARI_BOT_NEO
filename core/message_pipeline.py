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
import time
from typing import Protocol, runtime_checkable

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent

from core.lifecycle_logging import describe_event, elapsed_ms

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
        logger.info("已注册消息处理器: %s total=%d", handler.name, len(_handlers))


# =========================
# 核心 Pipeline Matcher
# =========================

msg_pipeline = on_message(priority=1, block=False)


@msg_pipeline.handle()
async def _pipeline_handle(bot: Bot, event: MessageEvent):
    """Pipeline 主入口：所有消息都经过此函数。"""

    from core.command_router import is_command_handled, mark_event_handled
    from core.error_notifier import notify_error_to_superuser, send_user_error

    # 输出 QQ 卡片消息的元数据到日志（方便调试卡片 URL 提取）
    _log_card_details(event)

    if is_command_handled(event):
        logger.debug("[Pipeline] 跳过已由命令处理的消息 %s", describe_event(event))
        return

    if not _handlers:
        logger.debug("[Pipeline] 无已注册消息处理器，跳过消息 %s", describe_event(event))
        return

    text = str(event.get_message())
    matched_any = False

    for handler in _handlers:
        try:
            matched = await handler.match(event, text)
        except Exception as e:
            logger.exception(f"Handler [{handler.name}] match() 异常: {e}")
            continue

        if not matched:
            continue

        matched_any = True
        logger.info(
            "[Pipeline] Handler 匹配成功 handler=%s registered_handlers=%d %s",
            handler.name,
            len(_handlers),
            describe_event(event, text),
        )
        mark_event_handled(event)
        started_at = time.monotonic()
        try:
            await handler.handle(bot, event)
        except Exception as e:
            logger.exception(
                "[Pipeline] Handler 处理异常 handler=%s elapsed=%.1fms: %s",
                handler.name,
                elapsed_ms(started_at),
                e,
            )
            try:
                await send_user_error(bot, event)
                await notify_error_to_superuser(bot, event, e, handler.name)
            except Exception as notify_err:
                logger.exception(f"发送错误通知失败: {notify_err}")
        else:
            logger.info(
                "[Pipeline] Handler 处理完成 handler=%s elapsed=%.1fms %s",
                handler.name,
                elapsed_ms(started_at),
                describe_event(event),
            )

        # 当前设计：一个消息可命中多个 handler（不 break）

    if not matched_any:
        logger.debug(
            "[Pipeline] 未命中任何消息处理器 registered_handlers=%d %s",
            len(_handlers),
            describe_event(event, text),
        )


def _log_card_details(event: MessageEvent) -> None:
    """将 QQ 分享卡片的元数据输出到日志，方便调试卡片 URL 提取。"""
    for segment in event.message:
        data = getattr(segment, "data", None)
        if not data:
            continue
        if getattr(segment, "type", "") != "json":
            continue

        raw_json = ""
        if isinstance(data, dict):
            raw_json = data.get("data", "") or ""
        elif isinstance(data, str):
            raw_json = data
        if not raw_json:
            continue

        try:
            import json as _json
            import sys as _sys

            card = _json.loads(raw_json) if isinstance(raw_json, str) else raw_json
            if not isinstance(card, dict):
                continue

            meta = card.get("meta") or {}
            if not isinstance(meta, dict):
                meta = {}

            detail_1 = meta.get("detail_1") or {}
            news = meta.get("news") or {}
            if not isinstance(detail_1, dict):
                detail_1 = {}
            if not isinstance(news, dict):
                news = {}

            app_name = str(card.get("app", ""))
            title = str(detail_1.get("title", "") or news.get("title", "") or "")
            desc = str(detail_1.get("desc", "") or news.get("desc", "") or "")
            jump_url = str(detail_1.get("qqdocurl", "") or news.get("jumpUrl", "") or "")

            meta_json = _json.dumps(meta, ensure_ascii=False, indent=2)
            logger.info(
                "[Pipeline] 📦 QQ 卡片详情:\n"
                "    app=%s\n"
                "    title=%s\n"
                "    desc=%s\n"
                "    jumpUrl=%s\n"
                "    rawMeta=%s",
                app_name, title, desc, jump_url, meta_json,
            )
            print(
                f"[Pipeline] 📦 QQ 卡片: app={app_name} title={title} "
                f"jumpUrl={jump_url}",
                file=_sys.stderr,
            )
        except Exception as e:
            logger.debug("[Pipeline] 卡片 JSON 解析失败: %s", e)
