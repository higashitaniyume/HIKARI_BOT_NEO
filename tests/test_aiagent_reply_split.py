"""AI Agent 回复切分与发送（`plugins/aiagent/reply.py`）测试。

覆盖：
- split_reply 段落 (\n\n)、单换行 (\n)、句末标点、逗号、空格以及硬切
- 切分后不丢字、不乱序
- URL 原子保护（不把 URL 从中间切断）
- 节点数超限处理（合并短段、超出截断并附加提示）
- send_reply 短回复走普通消息 + 引用
- send_reply 长回复走合并转发
- 合并转发失败降级为逐条发送
"""

from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from plugins.aiagent import reply as reply_mod
from plugins.aiagent.reply import build_reply_message, send_reply, send_with_timeout, split_reply


class FakeMessageSegment:
    def __init__(self, seg_type: str, data: dict[str, Any]) -> None:
        self.type = seg_type
        self.data = data


class FakeEvent:
    def __init__(self, user_id: int = 123456, group_id: int | None = None, message_id: int = 999) -> None:
        self.user_id = user_id
        self.group_id = group_id
        self.message_id = message_id

    def get_user_id(self) -> str:
        return str(self.user_id)


class FakeGroupEvent(FakeEvent):
    def __init__(self, user_id: int = 123456, group_id: int = 88888, message_id: int = 999) -> None:
        super().__init__(user_id=user_id, group_id=group_id, message_id=message_id)


class FakeBot:
    def __init__(self, self_id: str = "10000") -> None:
        self.self_id = self_id
        self.sent_messages: list[Any] = []
        self.api_calls: list[dict[str, Any]] = []
        self.api_error: Exception | None = None

    async def send(self, event: Any, message: Any) -> None:
        self.sent_messages.append({"event": event, "message": message})

    async def call_api(self, api: str, **kwargs: Any) -> Any:
        self.api_calls.append({"api": api, "kwargs": kwargs})
        if self.api_error is not None:
            raise self.api_error
        return {"status": "ok"}


class HangingBot(FakeBot):
    """模拟 NapCat 不回应 send_msg 的情况。"""

    async def send(self, event: Any, message: Any) -> None:
        await asyncio.sleep(30)

    async def call_api(self, api: str, **kwargs: Any) -> Any:
        await asyncio.sleep(30)


def _cfg(**overrides: Any) -> dict[str, Any]:
    chat_cfg = {
        "single_message_chars": 200,
        "max_reply_chars": 5000,
        "forward_node_chars": 100,
        "forward_max_nodes": 10,
        "quote_reply": True,
    }
    chat_cfg.update(overrides)
    return {"chat": chat_cfg}


class ReplySplitTests(unittest.IsolatedAsyncioTestCase):
    def test_split_by_paragraphs(self) -> None:
        text = "第一段内容很长很长很长很长很长很长。\n\n第二段内容也很长很长很长很长很长很长。\n\n第三段内容继续很长很长很长很长很长。"
        nodes = split_reply(text, node_chars=25, max_nodes=10)
        self.assertGreaterEqual(len(nodes), 3)
        self.assertEqual("".join(s.replace("\n\n", "") for s in nodes), text.replace("\n\n", ""))

    def test_split_by_sentences(self) -> None:
        text = "这是第一句话！这是第二句话？这是第三句话；这是第四句话。这是第五句话。"
        nodes = split_reply(text, node_chars=20, max_nodes=10)
        for node in nodes:
            self.assertLessEqual(len(node), 20)
        # 保证不丢字
        self.assertEqual("".join(nodes), text)

    def test_split_preserves_url(self) -> None:
        text = "请访问官网查看详情：https://example.com/very/long/path/to/resource 之后还有更多文字说明。"
        nodes = split_reply(text, node_chars=40, max_nodes=10)
        # 验证 URL 没有被拦腰切断
        combined = " ".join(nodes)
        self.assertIn("https://example.com/very/long/path/to/resource", combined)

    def test_split_exceeding_max_nodes_truncates(self) -> None:
        # 生成很多段落
        text = "\n\n".join(f"这是第 {i} 个长段落，里面有很多说明文字。" for i in range(50))
        nodes = split_reply(text, node_chars=50, max_nodes=5)
        self.assertLessEqual(len(nodes), 5)
        self.assertIn("（内容过长已截断）", nodes[-1])

    async def test_send_reply_short_message(self) -> None:
        bot = FakeBot()
        event = FakeEvent(user_id=123)
        cfg = _cfg(single_message_chars=200)

        short_text = "这是一条简短的回答。"
        await send_reply(bot, event, short_text, cfg)

        self.assertEqual(len(bot.sent_messages), 1)
        self.assertEqual(len(bot.api_calls), 0)

    async def test_send_reply_long_message_group_forward(self) -> None:
        bot = FakeBot()
        event = FakeGroupEvent(user_id=123, group_id=456)
        cfg = _cfg(single_message_chars=50, forward_node_chars=50)

        long_text = "这是一条超过单条阈值的长回复段落一。\n\n这是一条超过单条阈值的长回复段落二。\n\n这是一条超过单条阈值的长回复段落三。\n\n这是一条超过单条阈值的长回复段落四。"
        await send_reply(bot, event, long_text, cfg)

        self.assertEqual(len(bot.sent_messages), 0)
        self.assertEqual(len(bot.api_calls), 1)
        call = bot.api_calls[0]
        self.assertEqual(call["api"], "send_group_forward_msg")
        self.assertEqual(call["kwargs"]["group_id"], 456)
        messages = call["kwargs"]["messages"]
        self.assertGreater(len(messages), 1)

    async def test_send_reply_long_message_forward_fails_fallback_to_individual(self) -> None:
        bot = FakeBot()
        event = FakeGroupEvent(user_id=123, group_id=456)
        bot.api_error = RuntimeError("forward rejected")
        cfg = _cfg(single_message_chars=50, forward_node_chars=50)

        long_text = "这是一条超过单条阈值的长回复段落一。\n\n这是一条超过单条阈值的长回复段落二。\n\n这是一条超过单条阈值的长回复段落三。\n\n这是一条超过单条阈值的长回复段落四。"
        await send_reply(bot, event, long_text, cfg)

        # 降级为逐条普通消息发送
        self.assertGreaterEqual(len(bot.sent_messages), 2)

    async def test_send_with_timeout_returns_true_on_success(self) -> None:
        bot = FakeBot()
        event = FakeEvent()

        ok = await send_with_timeout(bot, event, "hello", timeout_seconds=0.1)

        self.assertTrue(ok)
        self.assertEqual(len(bot.sent_messages), 1)

    async def test_send_reply_short_message_times_out_without_hanging(self) -> None:
        bot = HangingBot()
        event = FakeEvent(user_id=123)
        cfg = _cfg(single_message_chars=200)

        with patch.object(reply_mod, "SEND_TIMEOUT_SECONDS", 0.01):
            await asyncio.wait_for(
                send_reply(bot, event, "这是一条会卡住的回复。", cfg),
                timeout=1.0,
            )

        self.assertEqual(bot.sent_messages, [])

    async def test_send_reply_forward_timeout_does_not_fallback(self) -> None:
        bot = HangingBot()
        event = FakeGroupEvent(user_id=123, group_id=456)
        cfg = _cfg(single_message_chars=50, forward_node_chars=50)
        long_text = "\n\n".join(f"这是一条超过单条阈值的长回复段落{i}。" for i in range(4))

        with patch.object(reply_mod, "SEND_TIMEOUT_SECONDS", 0.01):
            await asyncio.wait_for(
                send_reply(bot, event, long_text, cfg),
                timeout=1.0,
            )

        self.assertEqual(bot.sent_messages, [])
        self.assertEqual(bot.api_calls, [])


if __name__ == "__main__":
    unittest.main()
