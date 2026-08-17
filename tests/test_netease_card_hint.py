"""Netease card hint handler tests — 群聊网易云卡片引导 + 冷却."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    Message,
    PrivateMessageEvent,
)
from nonebot.adapters.onebot.v11.event import Sender

from plugins.netease_parser import NeteaseCardHintHandler, _card_hint_last


def _make_group(text: str = "", *, group_id: int = 111, at_self: bool = False) -> GroupMessageEvent:
    msg = Message(text)
    return GroupMessageEvent(
        time=2000, self_id="10000", post_type="message", message_type="group",
        sub_type="normal", group_id=group_id, user_id=10001, message_id=500,
        message=msg, raw_message=str(msg), font=0, sender=Sender(user_id=10001),
        to_me=at_self,
    )


def _make_private(text: str = "") -> PrivateMessageEvent:
    msg = Message(text)
    return PrivateMessageEvent(
        time=1000, self_id="10000", post_type="message", message_type="private",
        sub_type="friend", user_id=10001, message_id=100, message=msg,
        raw_message=str(msg), font=0, sender=Sender(user_id=10001),
    )


def _cfg(**overrides) -> dict:
    cfg = {
        "auto_parse": True,
        "auto_parse_groups": {"enable": False, "groups": []},
        "card_hint": {"enabled": True, "cooldown_seconds": 300},
        "permissions": {},
    }
    cfg.update(overrides)
    return cfg


class TestCardHintHandler(unittest.TestCase):
    def setUp(self):
        _card_hint_last.clear()

    async def _match(self, event, cfg=None):
        handler = NeteaseCardHintHandler()
        with patch(
            "plugins.netease_parser.get_config",
            return_value=cfg if cfg is not None else _cfg(),
        ):
            return await handler.match(event, str(event.get_message()))

    def test_group_link_no_at_triggers_hint(self):
        event = _make_group("https://music.163.com/song/33894312")
        self.assertTrue(asyncio.run(self._match(event)))

    def test_at_bot_no_hint(self):
        event = _make_group("https://music.163.com/song/33894312", at_self=True)
        self.assertFalse(asyncio.run(self._match(event)))

    def test_auto_parse_group_no_hint(self):
        event = _make_group("https://music.163.com/song/33894312")
        cfg = _cfg(auto_parse_groups={"enable": True, "groups": ["111"]})
        self.assertFalse(asyncio.run(self._match(event, cfg)))

    def test_private_no_hint(self):
        event = _make_private("https://music.163.com/song/33894312")
        self.assertFalse(asyncio.run(self._match(event)))

    def test_no_link_no_hint(self):
        event = _make_group("你好")
        self.assertFalse(asyncio.run(self._match(event)))

    def test_hint_disabled(self):
        event = _make_group("https://music.163.com/song/33894312")
        cfg = _cfg(card_hint={"enabled": False, "cooldown_seconds": 300})
        self.assertFalse(asyncio.run(self._match(event, cfg)))

    def test_hint_respects_cooldown(self):
        event = _make_group("https://music.163.com/song/33894312")
        handler = NeteaseCardHintHandler()
        with patch(
            "plugins.netease_parser.get_config", return_value=_cfg(),
        ):
            first = asyncio.run(handler.match(event, str(event.get_message())))
            self.assertTrue(first)
            # 首次命中后执行 handle，记录冷却时间
            asyncio.run(handler.handle(AsyncMock(), event))
            second = asyncio.run(handler.match(event, str(event.get_message())))
            self.assertFalse(second)


if __name__ == "__main__":
    unittest.main()
