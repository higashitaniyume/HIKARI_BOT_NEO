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
from plugins.aiagent import memory as memory_mod
from plugins.aiagent import quota as quota_mod
from plugins.aiagent import reply as reply_mod
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
        self.messages: list[Message] = []

    async def send(self, event: Any, message: Message) -> None:
        self.messages.append(message)
        # 引用段不算文本内容，这里只记录真正发出的文字
        self.sent.append(message.extract_plain_text())


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
        # 成功回复默认引用触发它的那条消息
        self.assertEqual(bot.messages[0][0].type, "reply")
        self.assertEqual(str(bot.messages[0][0].data["id"]), "1")

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

    async def test_send_failure_refunds_reserved_quota(self) -> None:
        cfg = self._quota_cfg()
        event = make_group_event(group_id="506", user_id="90001")

        class FailingBot:
            async def send(self, event: Any, message: Message) -> None:
                raise RuntimeError("NapCat result 120")

        async def ok(*args: Any, **kwargs: Any) -> str:
            return "你好呀"

        with (
            patch.object(aiagent, "get_config_for_event", return_value=cfg),
            patch.object(aiagent, "is_event_allowed", return_value=True),
            patch.object(aiagent, "request_chat_completion", ok),
            patch.object(reply_mod, "SEND_RETRY_DELAY_SECONDS", 0),
        ):
            await aiagent._handle_chat_event(FailingBot(), event, "你好")

        # 文字没发出去：不能算 delivered，预留下的配额必须退回
        self.assertEqual(quota_mod.get_quota_status(cfg, event)["daily"]["used"], 0)
        # 也不能把用户没看到的 assistant 回复写进下一轮上下文
        self.assertEqual(
            memory_mod.get_history(aiagent.session_key(event), 10, 12000),
            [],
        )


class PromptCachePrefixTests(unittest.IsolatedAsyncioTestCase):
    """时间戳放在 user 消息里，且写入历史的内容与发送内容逐字节一致。"""

    def setUp(self) -> None:
        aiagent._last_used_at.clear()
        memory_mod._histories.clear()

    def tearDown(self) -> None:
        memory_mod._histories.clear()

    async def test_sent_time_is_stored_and_prefix_is_stable(self) -> None:
        bot = RecordingBot()
        event = make_group_event(user_id="90002")
        captured: list[list[dict[str, Any]]] = []

        async def fake_reply(cfg: dict[str, Any], messages: list[dict[str, Any]], tool_context: Any = None) -> str:
            captured.append([dict(message) for message in messages])
            return "好的"

        with (
            patch.object(aiagent, "get_config_for_event", return_value=enabled_cfg()),
            patch.object(aiagent, "is_event_allowed", return_value=True),
            patch.object(aiagent, "request_chat_completion", fake_reply),
            patch.object(aiagent, "load_persona_prompt", return_value="人格"),
            patch.object(aiagent, "read_memory_context", return_value=""),
        ):
            await aiagent._handle_chat_event(bot, event, "你好")
            await aiagent._handle_chat_event(bot, event, "再问一句")

        first, second = captured
        self.assertTrue(str(first[-1]["content"]).startswith("【当前时间】"))
        self.assertTrue(str(second[-1]["content"]).startswith("【当前时间】"))
        # 上一轮实际发送的 user 内容必须原样进入下一轮历史，否则前缀对不上
        self.assertEqual(second[-3], {"role": "user", "content": first[-1]["content"]})
        self.assertEqual(second[-2], {"role": "assistant", "content": "好的"})
        # system prompt 逐字节不变
        self.assertEqual(first[0], second[0])


if __name__ == "__main__":
    unittest.main()
