"""AI Agent 错误分支与配额预留/退回的端到端行为。"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, PrivateMessageEvent

import plugins.aiagent as aiagent
from core.bot_messages import get_message as msg
from plugins.aiagent import quota as quota_mod
from plugins.aiagent.client import AIAgentRequestError


def make_group_event(group_id: str = "111", user_id: str = "222", self_id: str = "1") -> GroupMessageEvent:
    return GroupMessageEvent.model_validate(
        {
            "time": 1700000000,
            "self_id": int(self_id),
            "post_type": "message",
            "message_type": "group",
            "sub_type": "normal",
            "message_id": 1,
            "user_id": int(user_id),
            "group_id": int(group_id),
            "raw_message": "你好",
            "font": 0,
            "sender": {"user_id": int(user_id), "nickname": "tester", "role": "member"},
            "message": [{"type": "text", "data": {"text": "你好"}}],
        }
    )


def make_private_event(user_id: str = "333", self_id: str = "1") -> PrivateMessageEvent:
    return PrivateMessageEvent.model_validate(
        {
            "time": 1700000000,
            "self_id": int(self_id),
            "post_type": "message",
            "message_type": "private",
            "sub_type": "friend",
            "message_id": 1,
            "user_id": int(user_id),
            "raw_message": "你好",
            "font": 0,
            "sender": {"user_id": int(user_id), "nickname": "tester"},
            "message": [{"type": "text", "data": {"text": "你好"}}],
        }
    )


class RecordingBot:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, event: Any, message: Message) -> None:
        self.sent.append(str(message))


def enabled_cfg(**overrides: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "enabled": True,
        "chat": {"max_history_messages": 10, "cooldown_seconds": 0, "max_reply_chars": 3500},
        "memory": {"enabled": False},
        "tools": {"files": {"enabled": False}, "plugin_tools": {"enabled": False}},
        "quota": {"enabled": False},
        "permissions": {},
        "vision": {"enabled": False},
    }
    cfg.update(overrides)
    return cfg


class ErrorReplyTests(unittest.IsolatedAsyncioTestCase):
    """不同失败原因给出不同提示，而不是一律「回复失败」。"""

    async def _run(self, exc: Exception, cfg: dict[str, Any] | None = None) -> list[str]:
        bot = RecordingBot()
        event = make_group_event(user_id="90001")
        agent_cfg = cfg if cfg is not None else enabled_cfg()

        async def boom(*args: Any, **kwargs: Any) -> str:
            raise exc

        with (
            patch.object(aiagent, "get_config_for_event", return_value=agent_cfg),
            patch.object(aiagent, "is_event_allowed", return_value=True),
            patch.object(aiagent, "request_chat_completion", boom),
        ):
            await aiagent._handle_chat_event(bot, event, "你好")
        return bot.sent

    async def test_timeout_reply(self) -> None:
        sent = await self._run(httpx.ReadTimeout("timed out"))
        self.assertEqual(sent, [msg("aiagent.timeout")])

    async def test_network_error_reply(self) -> None:
        sent = await self._run(httpx.ConnectError("connection refused"))
        self.assertEqual(sent, [msg("aiagent.network_error")])

    async def test_rate_limited_reply(self) -> None:
        sent = await self._run(AIAgentRequestError(429, "too many requests"))
        self.assertEqual(sent, [msg("aiagent.rate_limited")])

    async def test_upstream_error_reply(self) -> None:
        sent = await self._run(AIAgentRequestError(502, "bad gateway"))
        self.assertEqual(sent, [msg("aiagent.upstream_error")])

    async def test_auth_failure_reply(self) -> None:
        sent = await self._run(AIAgentRequestError(401, "unauthorized"))
        self.assertEqual(sent, [msg("aiagent.auth_failed")])

    async def test_unexpected_failure_reply(self) -> None:
        sent = await self._run(RuntimeError("boom"))
        self.assertEqual(sent, [msg("aiagent.failed")])

    async def test_generic_error_reply_differs_from_specific_ones(self) -> None:
        """4xx（非 401/403/429）走通用失败提示，不要和超时/网络提示混淆。"""
        sent = await self._run(AIAgentRequestError(400, "bad request"))
        self.assertEqual(sent, [msg("aiagent.failed")])
        self.assertNotEqual(msg("aiagent.failed"), msg("aiagent.timeout"))
        self.assertNotEqual(msg("aiagent.failed"), msg("aiagent.network_error"))
        self.assertNotEqual(msg("aiagent.failed"), msg("aiagent.rate_limited"))
        self.assertNotEqual(msg("aiagent.failed"), msg("aiagent.upstream_error"))


class QuotaReservationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """配额在请求前预留：成功保留、失败退回、超限拦截。"""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_path = quota_mod.QUOTA_PATH
        quota_mod.QUOTA_PATH = Path(self._tmpdir.name) / "aiagent_quota.json"
        quota_mod.reset_usage_state()
        quota_mod._load_usage()

    def tearDown(self) -> None:
        quota_mod.QUOTA_PATH = self._orig_path
        quota_mod.reset_usage_state()
        self._tmpdir.cleanup()

    def _quota_cfg(self) -> dict[str, Any]:
        return enabled_cfg(
            quota={
                "enabled": True,
                "default_user": {"daily": 1, "hourly": 10},
                "default_group": {"daily": 1, "hourly": 10},
                "exempt_user_ids": [],
                "exempt_group_ids": [],
            }
        )

    async def test_success_keeps_reserved_quota(self) -> None:
        cfg = self._quota_cfg()
        bot = RecordingBot()
        event = make_group_event(group_id="501", user_id="90001")

        async def ok(*args: Any, **kwargs: Any) -> str:
            return "你好呀"

        with (
            patch.object(aiagent, "get_config_for_event", return_value=cfg),
            patch.object(aiagent, "is_event_allowed", return_value=True),
            patch.object(aiagent, "request_chat_completion", ok),
        ):
            await aiagent._handle_chat_event(bot, event, "你好")

        status = quota_mod.get_quota_status(cfg, event)
        self.assertEqual(status["daily"]["used"], 1)
        self.assertEqual(bot.sent, ["你好呀"])

    async def test_failure_refunds_reserved_quota(self) -> None:
        cfg = self._quota_cfg()
        bot = RecordingBot()
        event = make_group_event(group_id="502", user_id="90001")

        async def boom(*args: Any, **kwargs: Any) -> str:
            raise RuntimeError("boom")

        with (
            patch.object(aiagent, "get_config_for_event", return_value=cfg),
            patch.object(aiagent, "is_event_allowed", return_value=True),
            patch.object(aiagent, "request_chat_completion", boom),
        ):
            await aiagent._handle_chat_event(bot, event, "你好")

        self.assertEqual(quota_mod.get_quota_status(cfg, event)["daily"]["used"], 0)

    async def test_exhausted_quota_blocks_before_calling_model(self) -> None:
        cfg = self._quota_cfg()
        bot = RecordingBot()
        event = make_group_event(group_id="503", user_id="90001")
        quota_mod.record_usage(cfg, event, 1)

        called = False

        async def ok(*args: Any, **kwargs: Any) -> str:
            nonlocal called
            called = True
            return "不该被调用"

        with (
            patch.object(aiagent, "get_config_for_event", return_value=cfg),
            patch.object(aiagent, "is_event_allowed", return_value=True),
            patch.object(aiagent, "request_chat_completion", ok),
        ):
            await aiagent._handle_chat_event(bot, event, "你好")

        self.assertFalse(called)
        self.assertEqual(len(bot.sent), 1)
        self.assertIn("次数已用完", bot.sent[0])
        # who/period 已本地化，不会出现 "group"/"user" 原文
        self.assertIn("群聊", bot.sent[0])
        self.assertNotIn("group", bot.sent[0])

    async def test_concurrent_messages_cannot_exceed_daily_limit(self) -> None:
        cfg = self._quota_cfg()
        event = make_group_event(group_id="504", user_id="90001")

        started = 0

        async def slow_ok(*args: Any, **kwargs: Any) -> str:
            nonlocal started
            started += 1
            await asyncio.sleep(0.05)
            return "好"

        bots = [RecordingBot() for _ in range(4)]
        with (
            patch.object(aiagent, "get_config_for_event", return_value=cfg),
            patch.object(aiagent, "is_event_allowed", return_value=True),
            patch.object(aiagent, "request_chat_completion", slow_ok),
        ):
            # 同一会话（同群同用户）会被会话锁串行化，第二次直接在配额处被拦截
            await asyncio.gather(
                *(aiagent._handle_chat_event(bot, event, "你好") for bot in bots)
            )

        self.assertEqual(started, 1)
        self.assertEqual(quota_mod.get_quota_status(cfg, event)["daily"]["used"], 1)
        success_replies = [text for bot in bots for text in bot.sent if text == "好"]
        blocked_replies = [text for bot in bots for text in bot.sent if text != "好"]
        self.assertEqual(len(success_replies), 1)
        self.assertEqual(len(blocked_replies), 3)
        for text in blocked_replies:
            self.assertIn("次数已用完", text)

    async def test_parallel_users_in_group_share_group_quota_atomically(self) -> None:
        cfg = self._quota_cfg()
        group_id = "505"
        events = [make_group_event(group_id=group_id, user_id=f"9001{i}") for i in range(5)]

        async def slow_ok(*args: Any, **kwargs: Any) -> str:
            await asyncio.sleep(0.02)
            return "好"

        bots = [RecordingBot() for _ in events]
        with (
            patch.object(aiagent, "get_config_for_event", return_value=cfg),
            patch.object(aiagent, "is_event_allowed", return_value=True),
            patch.object(aiagent, "request_chat_completion", slow_ok),
        ):
            await asyncio.gather(
                *(aiagent._handle_chat_event(bot, event, "你好") for bot, event in zip(bots, events))
            )

        # 群配额每日 1 次：5 个不同用户并发也只有 1 个成功
        success_replies = [text for bot in bots for text in bot.sent if text == "好"]
        blocked_replies = [text for bot in bots for text in bot.sent if text != "好"]
        self.assertEqual(len(success_replies), 1)
        self.assertEqual(len(blocked_replies), 4)
        self.assertEqual(quota_mod.get_quota_status(cfg, events[0])["daily"]["used"], 1)


if __name__ == "__main__":
    unittest.main()
