from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from nonebot.adapters.onebot.v11 import ActionFailed, MessageSegment

from plugins.mention_reaction import (
    choose_emoji_id,
    parse_message_id,
    send_msg_emoji_like,
    should_react_to_empty_mention,
)
from plugins.poke_back import should_poke_back
from plugins.profile_like import extract_at_user_ids, handle_profile_like, parse_like_request


class _FakeEvent:
    def get_user_id(self) -> str:
        return "123456"

    def get_message(self) -> list[object]:
        return []


class _FakeCommandContext:
    args = ""
    bot = object()
    event = _FakeEvent()

    def __init__(self) -> None:
        self.sent: list[object] = []

    async def send(self, message: object) -> None:
        self.sent.append(message)


class ProfileLikeTests(unittest.TestCase):
    def test_default_likes_sender_to_full_amount(self) -> None:
        request = parse_like_request("", sender_id=123456, default_times=10, max_times=10)

        self.assertEqual(request.user_id, 123456)
        self.assertEqual(request.times, 10)
        self.assertFalse(request.explicit_target)

    def test_target_qq_and_times_are_parsed(self) -> None:
        request = parse_like_request("987654321 5", sender_id=123456, default_times=10, max_times=10)

        self.assertEqual(request.user_id, 987654321)
        self.assertEqual(request.times, 5)
        self.assertTrue(request.explicit_target)

    def test_plain_number_without_target_is_times(self) -> None:
        request = parse_like_request("5", sender_id=123456, default_times=10, max_times=10)

        self.assertEqual(request.user_id, 123456)
        self.assertEqual(request.times, 5)

    def test_at_target_wins_over_numeric_args(self) -> None:
        request = parse_like_request("5", sender_id=123456, at_user_ids=[888888], default_times=10, max_times=10)

        self.assertEqual(request.user_id, 888888)
        self.assertEqual(request.times, 5)
        self.assertTrue(request.explicit_target)

    def test_extract_at_user_ids_skips_all(self) -> None:
        message = [MessageSegment.at(123456), MessageSegment.at("all")]

        self.assertEqual(extract_at_user_ids(message), [123456])


class ProfileLikeCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_is_silent(self) -> None:
        ctx = _FakeCommandContext()

        with (
            patch("plugins.profile_like.get_config", return_value={"enabled": True, "default_times": 10, "max_times": 10}),
            patch("plugins.profile_like.send_profile_like", new_callable=AsyncMock) as send_like,
        ):
            await handle_profile_like(ctx)

        send_like.assert_awaited_once_with(ctx.bot, user_id=123456, times=10)
        self.assertEqual(ctx.sent, [])

    async def test_failure_is_silent(self) -> None:
        ctx = _FakeCommandContext()

        with (
            patch("plugins.profile_like.get_config", return_value={"enabled": True, "default_times": 10, "max_times": 10}),
            patch(
                "plugins.profile_like.send_profile_like",
                new_callable=AsyncMock,
                side_effect=ActionFailed(retcode=1200),
            ) as send_like,
        ):
            await handle_profile_like(ctx)

        send_like.assert_awaited_once_with(ctx.bot, user_id=123456, times=10)
        self.assertEqual(ctx.sent, [])

    async def test_unexpected_error_is_silent(self) -> None:
        ctx = _FakeCommandContext()

        with (
            patch("plugins.profile_like.get_config", return_value={"enabled": True, "default_times": 10, "max_times": 10}),
            patch(
                "plugins.profile_like.send_profile_like",
                new_callable=AsyncMock,
                side_effect=RuntimeError("boom"),
            ) as send_like,
        ):
            await handle_profile_like(ctx)

        send_like.assert_awaited_once_with(ctx.bot, user_id=123456, times=10)
        self.assertEqual(ctx.sent, [])


class PokeBackTests(unittest.TestCase):
    def test_pokes_back_when_bot_is_target(self) -> None:
        self.assertTrue(
            should_poke_back(
                actor_id=123,
                target_id=456,
                self_id=456,
                group_id=789,
                config={"enabled": True, "group_enabled": True},
            )
        )

    def test_ignores_pokes_not_targeting_bot(self) -> None:
        self.assertFalse(
            should_poke_back(
                actor_id=123,
                target_id=789,
                self_id=456,
                group_id=789,
                config={"enabled": True, "group_enabled": True},
            )
        )

    def test_respects_private_switch(self) -> None:
        self.assertFalse(
            should_poke_back(
                actor_id=123,
                target_id=456,
                self_id=456,
                group_id=None,
                config={"enabled": True, "private_enabled": False},
            )
        )


class MentionReactionTests(unittest.TestCase):
    def test_reacts_to_only_self_at_in_group(self) -> None:
        self.assertTrue(
            should_react_to_empty_mention(
                is_group=True,
                sender_id=123,
                self_id=456,
                group_id=789,
                message=[MessageSegment.at(456), MessageSegment.text("  ")],
                config={"enabled": True, "group_enabled": True, "emoji_ids": ["66"]},
            )
        )

    def test_ignores_text_after_mention(self) -> None:
        self.assertFalse(
            should_react_to_empty_mention(
                is_group=True,
                sender_id=123,
                self_id=456,
                group_id=789,
                message=[MessageSegment.at(456), MessageSegment.text("你好")],
                config={"enabled": True, "group_enabled": True, "emoji_ids": ["66"]},
            )
        )

    def test_ignores_non_text_segments(self) -> None:
        self.assertFalse(
            should_react_to_empty_mention(
                is_group=True,
                sender_id=123,
                self_id=456,
                group_id=789,
                message=[MessageSegment.at(456), MessageSegment.face(66)],
                config={"enabled": True, "group_enabled": True, "emoji_ids": ["66"]},
            )
        )

    def test_ignores_other_at_targets(self) -> None:
        self.assertFalse(
            should_react_to_empty_mention(
                is_group=True,
                sender_id=123,
                self_id=456,
                group_id=789,
                message=[MessageSegment.at(456), MessageSegment.at(888)],
                config={"enabled": True, "group_enabled": True, "emoji_ids": ["66"]},
            )
        )

    def test_respects_group_and_user_filters(self) -> None:
        self.assertFalse(
            should_react_to_empty_mention(
                is_group=True,
                sender_id=123,
                self_id=456,
                group_id=789,
                message=[MessageSegment.at(456)],
                config={"enabled": True, "allowed_groups": ["1000"], "ignored_users": []},
            )
        )
        self.assertFalse(
            should_react_to_empty_mention(
                is_group=True,
                sender_id=123,
                self_id=456,
                group_id=789,
                message=[MessageSegment.at(456)],
                config={"enabled": True, "allowed_groups": [], "ignored_users": ["123"]},
            )
        )

    def test_choose_emoji_uses_first_by_default(self) -> None:
        self.assertEqual(choose_emoji_id({"emoji_ids": ["66", "76"], "random": False}), "66")

    def test_parse_message_id_rejects_non_numeric_value(self) -> None:
        self.assertEqual(parse_message_id("12345"), 12345)
        self.assertIsNone(parse_message_id("abc"))


class MentionReactionApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_msg_emoji_like_uses_napcat_api(self) -> None:
        class FakeBot:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, object]]] = []

            async def call_api(self, action: str, **data: object) -> None:
                self.calls.append((action, data))

        bot = FakeBot()

        await send_msg_emoji_like(bot, message_id=12345, emoji_id="66")

        self.assertEqual(bot.calls, [("set_msg_emoji_like", {"message_id": 12345, "emoji_id": "66"})])


if __name__ == "__main__":
    unittest.main()
