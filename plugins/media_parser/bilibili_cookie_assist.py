"""OneBot admin assist flow for refreshing Bilibili cookies."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment

from core.bot_messages import get_message as msg
from core.config_loader import load_main_config

logger = logging.getLogger("HikariBot.MediaParserBilibiliCookieAssist")

CONFIRM_TEXT = "确定"
_PLACEHOLDER_SUPERUSER = {"", "你的QQ号"}
_REASON_LABELS = {
    "missing_cookie": "未配置可用 Cookie",
    "cookie_invalid": "Cookie 已失效或无效",
    "cookie_unavailable": "Cookie 不可用",
}


class BilibiliCookieAssistManager:
    """Small state machine for Bilibili QR login in OneBot private chat."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._waiting_confirm = False
        self._confirm_deadline = 0.0
        self._last_request_at = 0.0
        self._pending_reason = ""
        self._pending_auth_runtime: Any | None = None
        self._reply_timeout_seconds = 0
        self._request_cooldown_seconds = 0
        self._tasks: set[asyncio.Task[None]] = set()

    def _new_task(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._finish_task)

    def _finish_task(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        try:
            error = task.exception()
        except Exception as e:
            logger.warning("[bilibili] 读取 Cookie 协助后台任务状态失败: %s", e)
            return
        if error is not None:
            logger.warning("[bilibili] Cookie 协助后台任务异常: %s", error)

    @staticmethod
    def _superuser_id() -> str:
        try:
            cfg = load_main_config()
        except Exception as e:
            logger.warning("[bilibili] 读取超级管理员配置失败: %s", e)
            return ""
        return str(cfg.get("bot", {}).get("superuser_id") or "").strip()

    @staticmethod
    def _reason_label(reason: str) -> str:
        reason = str(reason or "cookie_unavailable").strip()
        return _REASON_LABELS.get(reason, reason)

    @staticmethod
    def _minutes(seconds: int) -> int:
        return max(1, int(seconds / 60))

    @staticmethod
    def _is_superuser_private_event(event: MessageEvent, superuser_id: str) -> bool:
        if not superuser_id or isinstance(event, GroupMessageEvent):
            return False
        try:
            return str(event.get_user_id()).strip() == superuser_id
        except Exception:
            return False

    @staticmethod
    async def _send_private_text(bot: Bot, superuser_id: str, text: str) -> None:
        await bot.send_private_msg(user_id=int(superuser_id), message=Message(text))

    @staticmethod
    async def _send_private_image(bot: Bot, superuser_id: str, image_url: str) -> None:
        await bot.send_private_msg(
            user_id=int(superuser_id),
            message=Message(MessageSegment.image(image_url)),
        )

    def should_handle_reply(self, event: MessageEvent) -> bool:
        if not self._waiting_confirm:
            return False
        return self._is_superuser_private_event(event, self._superuser_id())

    def trigger_assist_request(
        self,
        bot: Bot,
        *,
        reason: str,
        auth_runtime: Any,
        reply_timeout_minutes: int,
        request_cooldown_minutes: int,
    ) -> None:
        """Queue a private confirmation prompt to the configured superuser."""
        superuser_id = self._superuser_id()
        if superuser_id in _PLACEHOLDER_SUPERUSER:
            logger.warning("[bilibili] superuser_id 未配置，无法发送 Cookie 协助登录请求。")
            return
        try:
            int(superuser_id)
        except ValueError:
            logger.warning("[bilibili] superuser_id 不是有效 QQ 号，无法发送 Cookie 协助登录请求。")
            return

        self._new_task(self._trigger_assist_request(
            bot,
            superuser_id=superuser_id,
            reason=reason,
            auth_runtime=auth_runtime,
            reply_timeout_minutes=reply_timeout_minutes,
            request_cooldown_minutes=request_cooldown_minutes,
        ))

    async def _trigger_assist_request(
        self,
        bot: Bot,
        *,
        superuser_id: str,
        reason: str,
        auth_runtime: Any,
        reply_timeout_minutes: int,
        request_cooldown_minutes: int,
    ) -> None:
        reply_timeout_seconds = max(1, int(reply_timeout_minutes) * 60)
        request_cooldown_seconds = max(1, int(request_cooldown_minutes) * 60)
        now = time.time()

        async with self._lock:
            if self._waiting_confirm:
                return
            if now - self._last_request_at < request_cooldown_seconds:
                return

            self._waiting_confirm = True
            self._confirm_deadline = now + reply_timeout_seconds
            self._last_request_at = now
            self._pending_reason = reason or "cookie_unavailable"
            self._pending_auth_runtime = auth_runtime
            self._reply_timeout_seconds = reply_timeout_seconds
            self._request_cooldown_seconds = request_cooldown_seconds

        try:
            await self._send_private_text(
                bot,
                superuser_id,
                msg(
                    "media_parser.bilibili_cookie_assist_prompt",
                    reason=self._reason_label(reason),
                    minutes=self._minutes(reply_timeout_seconds),
                ),
            )
        except Exception as e:
            logger.warning("[bilibili] 向超级管理员发送 Cookie 协助请求失败: %s", e)
            async with self._lock:
                self._waiting_confirm = False
                self._pending_auth_runtime = None

    async def handle_reply(self, bot: Bot, event: MessageEvent) -> bool:
        """Handle a superuser private reply. Returns whether it consumed the event."""
        superuser_id = self._superuser_id()
        if not self._is_superuser_private_event(event, superuser_id):
            return False

        async with self._lock:
            if not self._waiting_confirm:
                return False

            now = time.time()
            if now > self._confirm_deadline:
                self._waiting_confirm = False
                self._pending_auth_runtime = None
                await self._send_private_text(bot, superuser_id, msg("media_parser.bilibili_cookie_assist_expired"))
                return True

            text = event.get_plaintext().strip()
            if text != CONFIRM_TEXT:
                self._waiting_confirm = False
                self._pending_auth_runtime = None
                await self._send_private_text(bot, superuser_id, msg("media_parser.bilibili_cookie_assist_canceled"))
                return True

            auth_runtime = self._pending_auth_runtime
            timeout_seconds = self._reply_timeout_seconds
            self._waiting_confirm = False
            self._pending_auth_runtime = None

        if auth_runtime is None:
            await self._send_private_text(bot, superuser_id, msg("media_parser.bilibili_cookie_assist_runtime_missing"))
            return True

        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                payload = await auth_runtime.generate_login_payload(session)
        except Exception as e:
            logger.warning("[bilibili] 生成超级管理员协助登录二维码失败: %s", e)
            await self._send_private_text(bot, superuser_id, msg("media_parser.bilibili_cookie_assist_generate_failed"))
            return True

        await self._send_private_text(
            bot,
            superuser_id,
            msg(
                "media_parser.bilibili_cookie_assist_login_text",
                login_url=payload["login_url"],
            ),
        )
        try:
            await self._send_private_image(bot, superuser_id, payload["qr_code_url"])
        except Exception as e:
            logger.warning("[bilibili] 发送超级管理员协助登录二维码图片失败: %s", e)
            await self._send_private_text(
                bot,
                superuser_id,
                msg(
                    "media_parser.bilibili_cookie_assist_image_failed",
                    qr_code_url=payload["qr_code_url"],
                ),
            )

        self._new_task(self._poll_login_and_notify(
            bot,
            superuser_id=superuser_id,
            auth_runtime=auth_runtime,
            qrcode_key=payload["qrcode_key"],
            timeout_seconds=timeout_seconds,
        ))
        return True

    async def _poll_login_and_notify(
        self,
        bot: Bot,
        *,
        superuser_id: str,
        auth_runtime: Any,
        qrcode_key: str,
        timeout_seconds: int,
    ) -> None:
        timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                result = await auth_runtime.poll_login_until_complete(
                    session=session,
                    qrcode_key=qrcode_key,
                    timeout_seconds=timeout_seconds,
                )
        except Exception as e:
            logger.warning("[bilibili] 超级管理员协助登录轮询失败: %s", e)
            await self._send_private_text_safe(
                bot,
                superuser_id,
                msg("media_parser.bilibili_cookie_assist_poll_failed"),
            )
            return

        status = result.get("status")
        if status == "success":
            await self._send_private_text_safe(
                bot,
                superuser_id,
                msg("media_parser.bilibili_cookie_assist_success"),
            )
        elif status == "expired":
            await self._send_private_text_safe(
                bot,
                superuser_id,
                msg("media_parser.bilibili_cookie_assist_qr_expired"),
            )
        else:
            await self._send_private_text_safe(
                bot,
                superuser_id,
                msg("media_parser.bilibili_cookie_assist_timeout"),
            )

    async def _send_private_text_safe(self, bot: Bot, superuser_id: str, text: str) -> None:
        try:
            await self._send_private_text(bot, superuser_id, text)
        except Exception as e:
            logger.warning("[bilibili] 向超级管理员发送 Cookie 协助结果失败: %s", e)


bilibili_cookie_assist = BilibiliCookieAssistManager()
