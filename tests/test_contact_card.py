from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

from nonebot.adapters.onebot.v11 import (
    ActionFailed,
    GroupMessageEvent,
    Message,
    PrivateMessageEvent,
)
from nonebot.utils import DataclassEncoder

from plugins.contact_card import (
    InvalidQQError,
    build_contact_segment,
    handle_contact_card,
    parse_qq,
)


def _private_event(user_id: int) -> PrivateMessageEvent:
    return PrivateMessageEvent(
        time=0,
        self_id=1,
        post_type="message",
        message_type="private",
        sub_type="friend",
        font=0,
        sender={"user_id": user_id},
        user_id=user_id,
        message_id=2,
        raw_message="名片 123456789",
        message=[{"type": "text", "data": {"text": "名片 123456789"}}],
    )


def _group_event(*, user_id: int, group_id: int) -> GroupMessageEvent:
    return GroupMessageEvent(
        time=0,
        self_id=1,
        post_type="message",
        message_type="group",
        sub_type="normal",
        font=0,
        sender={"user_id": user_id},
        user_id=user_id,
        group_id=group_id,
        message_id=3,
        raw_message="名片 123456789",
        message=[{"type": "text", "data": {"text": "名片 123456789"}}],
    )


class _FakeCommandContext:
    bot = object()
    event = object()

    def __init__(self, args: str = "") -> None:
        self.args = args
        self.sent: list[Message] = []

    async def send(self, message: Message) -> None:
        self.sent.append(message)


class _FailingContext(_FakeCommandContext):
    def __init__(self, args: str, error: Exception) -> None:
        super().__init__(args)
        self.error = error
        self.attempts: list[Message] = []

    async def send(self, message: Message) -> None:
        self.attempts.append(message)
        # 第一次（名片消息段）失败，后续的文本提示照常记录。
        if len(self.attempts) == 1:
            raise self.error
        self.sent.append(message)


def _first_segment(message: Message) -> object:
    return list(message)[0]


class ParseQQTests(unittest.TestCase):
    def test_valid_qq_is_parsed(self) -> None:
        self.assertEqual(parse_qq("123456789"), 123456789)

    def test_surrounding_and_inner_whitespace_is_ignored(self) -> None:
        self.assertEqual(parse_qq("  123 456 789 "), 123456789)

    def test_fullwidth_digits_are_normalized(self) -> None:
        self.assertEqual(parse_qq("１２３４５６７８９"), 123456789)

    def test_empty_args_raise_usage(self) -> None:
        with self.assertRaises(InvalidQQError) as ctx:
            parse_qq("")
        self.assertEqual(ctx.exception.message_key, "contact_card.usage")

    def test_non_numeric_args_raise_not_numeric(self) -> None:
        for value in ("abc", "12a34", "123456789.", "-123456", "12_34567"):
            with self.subTest(value=value), self.assertRaises(InvalidQQError) as ctx:
                parse_qq(value)
            self.assertEqual(ctx.exception.message_key, "contact_card.not_numeric")

    def test_length_bounds_are_enforced(self) -> None:
        for value in ("123", "123456789012"):
            with self.subTest(value=value), self.assertRaises(InvalidQQError) as ctx:
                parse_qq(value)
            self.assertEqual(ctx.exception.message_key, "contact_card.bad_length")

    def test_bounds_are_configurable(self) -> None:
        self.assertEqual(parse_qq("1234", min_digits=4, max_digits=11), 1234)

    def test_ten_and_eleven_digit_qq_are_accepted(self) -> None:
        self.assertEqual(parse_qq("1234567890"), 1234567890)
        self.assertEqual(parse_qq("12345678901"), 12345678901)


class BuildContactSegmentTests(unittest.TestCase):
    def test_segment_matches_onebot_contact_spec(self) -> None:
        segment = build_contact_segment(123456789)

        self.assertEqual(segment.type, "contact")
        self.assertEqual(segment.data, {"type": "qq", "id": "123456789"})

    def test_fallback_construction_produces_same_payload(self) -> None:
        from nonebot.adapters.onebot.v11 import MessageSegment

        # 模拟适配器没有 contact_user 工厂方法的情况。
        with patch.object(MessageSegment, "contact_user", None):
            segment = build_contact_segment(123456789)

        self.assertEqual(segment.type, "contact")
        self.assertEqual(segment.data, {"type": "qq", "id": "123456789"})


class HandleContactCardTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_contact_segment_only(self) -> None:
        ctx = _FakeCommandContext("123456789")

        await handle_contact_card(ctx)

        self.assertEqual(len(ctx.sent), 1)
        message = ctx.sent[0]
        self.assertEqual(len(list(message)), 1)
        segment = _first_segment(message)
        self.assertEqual(segment.type, "contact")
        self.assertEqual(segment.data, {"type": "qq", "id": "123456789"})

    async def test_missing_argument_replies_with_usage(self) -> None:
        ctx = _FakeCommandContext("")

        await handle_contact_card(ctx)

        self.assertEqual(str(ctx.sent[0]), "用法：名片 QQ号")

    async def test_non_numeric_argument_replies_with_hint(self) -> None:
        ctx = _FakeCommandContext("abc")

        await handle_contact_card(ctx)

        self.assertEqual(str(ctx.sent[0]), "QQ号必须是纯数字。")

    async def test_action_failed_replies_with_send_failed(self) -> None:
        ctx = _FailingContext("123456789", ActionFailed(retcode=1404))

        await handle_contact_card(ctx)

        self.assertEqual(str(ctx.sent[0]), "名片发送失败，请检查 NapCat 是否正常连接。")

    async def test_unexpected_error_replies_with_send_failed(self) -> None:
        ctx = _FailingContext("123456789", RuntimeError("ws closed"))

        await handle_contact_card(ctx)

        self.assertEqual(str(ctx.sent[0]), "名片发送失败，请检查 NapCat 是否正常连接。")

    async def test_reply_failure_does_not_propagate(self) -> None:
        ctx = _FakeCommandContext("123456789")
        ctx.send = AsyncMock(side_effect=RuntimeError("ws closed"))  # type: ignore[method-assign]

        await handle_contact_card(ctx)

        self.assertEqual(ctx.send.await_count, 2)

    async def test_disabled_plugin_sends_nothing(self) -> None:
        ctx = _FakeCommandContext("123456789")

        with patch(
            "plugins.contact_card.get_config",
            return_value={"enabled": False, "min_digits": 5, "max_digits": 11},
        ):
            await handle_contact_card(ctx)

        self.assertEqual(ctx.sent, [])


class OneBotPayloadTests(unittest.IsolatedAsyncioTestCase):
    """验证最终发给 NapCat 的 OneBot JSON，以及发送目标是否按 Event 自动路由。"""

    async def _send_and_capture(self, event: object) -> dict:
        from nonebot.adapters.onebot.v11.bot import send as adapter_send

        calls: list[dict] = []

        class _FakeBot:
            async def send_msg(self, **params: object) -> None:
                calls.append(params)

        await adapter_send(_FakeBot(), event, Message(build_contact_segment(123456789)))
        return calls[0]

    def _assert_contact_payload(self, params: dict) -> None:
        # 复用适配器真实的序列化器，确认 WS 上的 JSON 结构符合 OneBot V11 contact 规范。
        payload = json.loads(json.dumps(params["message"], cls=DataclassEncoder))
        self.assertEqual(payload, [{"type": "contact", "data": {"type": "qq", "id": "123456789"}}])

    async def test_private_event_routes_to_sender(self) -> None:
        params = await self._send_and_capture(_private_event(111))

        self.assertEqual(params["message_type"], "private")
        self.assertEqual(params["user_id"], 111)
        self.assertNotIn("group_id", params)
        self._assert_contact_payload(params)

    async def test_group_event_routes_to_group(self) -> None:
        params = await self._send_and_capture(_group_event(user_id=111, group_id=222))

        self.assertEqual(params["message_type"], "group")
        self.assertEqual(params["group_id"], 222)
        self._assert_contact_payload(params)


if __name__ == "__main__":
    unittest.main()
