"""AI 回复 @ 标记渲染（`plugins/aiagent/mention.py`）测试。"""

from __future__ import annotations

import unittest
from typing import Any

from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent

from plugins.aiagent import mention
from plugins.aiagent.reply import build_reply_message, send_reply

GROUP_ID = 10001
USER_ID = 20001
BOT_ID = 99999
TARGET_ID = 123456
EVENT_TIME = 1700000000


def _cfg(**mention_overrides: Any) -> dict[str, Any]:
    options = {"enabled": True, "max_per_reply": 3}
    options.update(mention_overrides)
    return {"tools": {"group_mention": options}}


def _group_event() -> GroupMessageEvent:
    return GroupMessageEvent(
        time=EVENT_TIME,
        self_id=BOT_ID,
        post_type="message",
        message_type="group",
        sub_type="normal",
        message_id=1,
        group_id=GROUP_ID,
        user_id=USER_ID,
        raw_message="hi",
        font=0,
        sender={"user_id": USER_ID, "nickname": "nick", "card": "", "role": "member"},
        message=[{"type": "text", "data": {"text": "hi"}}],
    )


def _private_event() -> PrivateMessageEvent:
    return PrivateMessageEvent(
        time=EVENT_TIME,
        self_id=BOT_ID,
        post_type="message",
        message_type="private",
        sub_type="friend",
        message_id=1,
        user_id=USER_ID,
        raw_message="hi",
        font=0,
        sender={"user_id": USER_ID, "nickname": "nick"},
        message=[{"type": "text", "data": {"text": "hi"}}],
    )


def _segment_types(message: Any) -> list[str]:
    return [segment.type for segment in message]


def _at_targets(message: Any) -> list[str]:
    return [
        str(segment.data.get("qq"))
        for segment in message
        if segment.type == "at"
    ]


class RenderPartsTests(unittest.TestCase):
    def test_renders_at_parts(self) -> None:
        parts = mention.render_parts(
            "你好 [[at:123456]] 在吗", enabled=True, max_per_reply=3
        )
        self.assertEqual(
            parts, [("text", "你好 "), ("at", "123456"), ("text", " 在吗")]
        )

    def test_disabled_strips_markers(self) -> None:
        parts = mention.render_parts(
            "你好 [[at:123456]] 在吗", enabled=False, max_per_reply=3
        )
        self.assertEqual(parts, [("text", "你好  在吗")])

    def test_extra_markers_are_dropped(self) -> None:
        parts = mention.render_parts(
            "[[at:111111]][[at:222222]][[at:333333]]",
            enabled=True,
            max_per_reply=2,
        )
        self.assertEqual(
            [part for part in parts if part[0] == "at"],
            [("at", "111111"), ("at", "222222")],
        )

    def test_malformed_markers_are_removed(self) -> None:
        for raw in ("[[at:all]]", "[[at:123]]", "[[at:abc]]"):
            parts = mention.render_parts(
                f"你好 {raw}！", enabled=True, max_per_reply=3
            )
            self.assertNotIn("[[at:", "".join(value for _, value in parts))

    def test_tolerates_spaces_and_fullwidth_colon(self) -> None:
        for raw in ("[[at: 123456 ]]", "[[at：123456]]", "[[ at : 123456 ]]"):
            parts = mention.render_parts(
                f"你好 {raw}", enabled=True, max_per_reply=3
            )
            self.assertIn(("at", "123456"), parts, raw)

    def test_zero_limit_renders_no_at(self) -> None:
        parts = mention.render_parts(
            "[[at:123456]]", enabled=True, max_per_reply=0
        )
        self.assertEqual(parts, [])

    def test_empty_text(self) -> None:
        self.assertEqual(mention.render_parts("", enabled=True, max_per_reply=3), [])


class BuildMessageTests(unittest.TestCase):
    def test_group_mention_becomes_at_segment(self) -> None:
        message = mention.build_message(
            f"你好 [[at:{TARGET_ID}]] 在吗", _cfg(), group=True
        )
        self.assertEqual(_segment_types(message), ["text", "at", "text"])
        self.assertEqual(_at_targets(message), [str(TARGET_ID)])

    def test_private_chat_strips_marker(self) -> None:
        message = mention.build_message(
            f"你好 [[at:{TARGET_ID}]]", _cfg(), group=False
        )
        self.assertEqual(_segment_types(message), ["text"])
        self.assertNotIn("[[at:", str(message))

    def test_disabled_config_strips_marker(self) -> None:
        message = mention.build_message(
            f"你好 [[at:{TARGET_ID}]]", _cfg(enabled=False), group=True
        )
        self.assertEqual(_segment_types(message), ["text"])
        self.assertNotIn("[[at:", str(message))

    def test_max_per_reply_is_applied(self) -> None:
        text = " ".join(f"[[at:{TARGET_ID + i}]]" for i in range(5))
        message = mention.build_message(text, _cfg(max_per_reply=2), group=True)
        self.assertEqual(len(_at_targets(message)), 2)

    def test_defaults_when_config_missing(self) -> None:
        self.assertTrue(mention.mentions_enabled(None))
        self.assertTrue(mention.mentions_enabled({}))
        self.assertEqual(mention.max_mentions({}), 3)


class ReplyIntegrationTests(unittest.TestCase):
    def test_build_reply_message_renders_mention(self) -> None:
        message = build_reply_message(
            _group_event(), f"[[at:{TARGET_ID}]] 看这里", _cfg()
        )
        self.assertEqual(_segment_types(message), ["reply", "at", "text"])
        self.assertEqual(_at_targets(message), [str(TARGET_ID)])

    def test_build_reply_message_private_strips_mention(self) -> None:
        message = build_reply_message(
            _private_event(), f"[[at:{TARGET_ID}]] 看这里", _cfg()
        )
        self.assertNotIn("at", _segment_types(message))
        self.assertNotIn("[[at:", message.extract_plain_text())


class FakeBot:
    def __init__(self) -> None:
        self.self_id = str(BOT_ID)
        self.api_calls: list[tuple[str, dict[str, Any]]] = []
        self.sent: list[Any] = []

    async def send(self, event: Any, message: Any) -> None:
        self.sent.append(message)

    async def call_api(self, api: str, **kwargs: Any) -> Any:
        self.api_calls.append((api, kwargs))
        return {"status": "ok"}


class ForwardIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_forward_nodes_render_mentions(self) -> None:
        bot = FakeBot()
        cfg = {
            "chat": {
                "single_message_chars": 10,
                "max_reply_chars": 5000,
                "forward_node_chars": 100,
                "forward_max_nodes": 10,
                "quote_reply": True,
            },
            "tools": {"group_mention": {"enabled": True, "max_per_reply": 3}},
        }
        text = f"这是一段很长的回复内容。[[at:{TARGET_ID}]] 后面还有更多说明文字。"
        sent = await send_reply(bot, _group_event(), text, cfg)
        self.assertTrue(sent)
        api, kwargs = bot.api_calls[-1]
        self.assertEqual(api, "send_group_forward_msg")
        contents = [
            node.data.get("content")
            for node in kwargs["messages"]
            if getattr(node, "type", "") == "node"
        ]
        at_targets = [
            str(segment.data.get("qq"))
            for content in contents
            for segment in content
            if segment.type == "at"
        ]
        self.assertEqual(at_targets, [str(TARGET_ID)])

    async def test_short_reply_renders_mentions(self) -> None:
        bot = FakeBot()
        cfg = {
            "chat": {"single_message_chars": 500, "quote_reply": False},
            "tools": {"group_mention": {"enabled": True, "max_per_reply": 3}},
        }
        await send_reply(bot, _group_event(), f"[[at:{TARGET_ID}]] 你好", cfg)
        self.assertEqual(_at_targets(bot.sent[-1]), [str(TARGET_ID)])


class GroupPromptTests(unittest.TestCase):
    def test_group_prompt_mentions_marker_when_enabled(self) -> None:
        from plugins.aiagent import _group_tools_prompt

        prompt = _group_tools_prompt(
            {"tools": {"group_mention": {"enabled": True}}}, _group_event()
        )
        self.assertIn("[[at:QQ号]]", prompt)

    def test_group_prompt_empty_in_private(self) -> None:
        from plugins.aiagent import _group_tools_prompt

        self.assertEqual(_group_tools_prompt({}, _private_event()), "")

    def test_group_prompt_absent_when_tools_disabled(self) -> None:
        from plugins.aiagent import _group_tools_prompt

        cfg = {
            "tools": {
                "group_mute": {"enabled": False},
                "group_kick": {"enabled": False},
                "group_poke": {"enabled": False},
                "group_mention": {"enabled": False},
            }
        }
        self.assertEqual(_group_tools_prompt(cfg, _group_event()), "")


if __name__ == "__main__":
    unittest.main()
