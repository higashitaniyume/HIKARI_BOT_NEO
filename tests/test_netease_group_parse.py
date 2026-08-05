"""Netease group manual-parse trigger tests — @bot + previous 10 messages."""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.adapters.onebot.v11.event import Sender

from plugins.netease_parser import (
    AutoNeteaseHandler,
    _history_before_trigger,
    _history_event,
)
from plugins.netease_parser.parser import extract_song_ids_from_event


def _make_private(text: str) -> PrivateMessageEvent:
    msg = Message(text)
    return PrivateMessageEvent(
        time=1000, self_id="10000", post_type="message", message_type="private",
        sub_type="friend", user_id=10001, message_id=100, message=msg,
        raw_message=str(msg), font=0, sender=Sender(user_id=10001),
    )


def _make_group(
    text: str = "",
    *,
    group_id: int = 111,
    at_self: bool = True,
    message_id: int = 500,
    time: int = 2000,
) -> GroupMessageEvent:
    segments = []
    if at_self:
        segments.append(MessageSegment.at("10000"))
    if text:
        segments.append(MessageSegment.text(text))
    msg = Message(segments)
    return GroupMessageEvent(
        time=time, self_id="10000", post_type="message", message_type="group",
        sub_type="normal", group_id=group_id, user_id=10001, message_id=message_id,
        message=msg, raw_message=str(msg), font=0, sender=Sender(user_id=10001),
    )


def _cfg(auto_enable: bool = False, groups=("111",)) -> dict:
    """auto_enable=True 且群在 groups 内 → 该群自动解析；否则手动解析（@触发）。"""
    return {
        "auto_parse": True,
        "auto_parse_groups": {"enable": auto_enable, "groups": list(groups)},
        "permissions": {},
    }


def _history_item(message_id: int, time: int, text: str) -> dict:
    return {
        "message_id": message_id,
        "time": time,
        "message": [{"type": "text", "data": {"text": text}}],
    }


class TestGroupParseTrigger(unittest.TestCase):
    async def _match(self, event, cfg=None):
        handler = AutoNeteaseHandler()
        with patch(
            "plugins.netease_parser.get_config",
            return_value=cfg if cfg is not None else _cfg(),
        ), patch(
            "nonebot.get_bot", return_value=SimpleNamespace(self_id="10000"),
        ):
            return await handler.match(event, str(event.get_message()))

    def test_private_link_matches(self):
        event = _make_private("https://music.163.com/song/33894312")
        self.assertTrue(asyncio.run(self._match(event)))

    def test_private_no_link_not_matched(self):
        event = _make_private("你好")
        self.assertFalse(asyncio.run(self._match(event)))

    def test_group_link_no_at_default_manual_not_matched(self):
        # 默认（未配置自动解析）：群聊有链接但无人 @ → 不解析
        event = _make_group("https://music.163.com/song/33894312", at_self=False)
        self.assertFalse(asyncio.run(self._match(event, _cfg(auto_enable=False))))

    def test_auto_parse_group_link_no_at_matches(self):
        # 管理员配置的自动解析群：有链接直接自动解析，无需 @
        event = _make_group("https://music.163.com/song/33894312", at_self=False)
        self.assertTrue(asyncio.run(self._match(event, _cfg(auto_enable=True))))

    def test_group_not_in_auto_list_stays_manual(self):
        # 自动解析列表里有别的群 → 本群仍为手动解析（无 @ 不解析）
        event = _make_group("https://music.163.com/song/33894312", group_id=222, at_self=False)
        self.assertFalse(asyncio.run(self._match(event, _cfg(auto_enable=True))))

    def test_default_manual_group_at_with_link_matches(self):
        # 默认手动解析群：@ + 自身带链接 → 解析
        event = _make_group("https://music.163.com/song/33894312")
        self.assertTrue(asyncio.run(self._match(event, _cfg(auto_enable=False))))

    def test_group_at_no_link_history_has_link_matches(self):
        event = _make_group("帮我解析")
        history = [
            _history_item(10, 1000, "前面有人发 https://music.163.com/song/33894312"),
        ]
        with patch(
            "plugins.netease_parser._get_group_history",
            new=AsyncMock(return_value=history),
        ) as gh:
            result = asyncio.run(self._match(event))
            self.assertTrue(result)
            gh.assert_awaited_once()

    def test_group_at_no_link_history_no_link_not_matched(self):
        event = _make_group("你好")
        with patch(
            "plugins.netease_parser._get_group_history",
            new=AsyncMock(return_value=[]),
        ):
            self.assertFalse(asyncio.run(self._match(event)))

    def test_group_at_history_card_link_matches(self):
        # 历史消息带 QQ 音乐卡片（json 段）
        event = _make_group("解析一下")
        card = {
            "app": "com.tencent.music.lua",
            "meta": {"music": {"jumpUrl": "https://music.163.com/song/33894312"}},
        }
        history = [{
            "message_id": 10,
            "time": 1000,
            "message": [{"type": "json", "data": {"data": json.dumps(card)}}],
        }]
        with patch(
            "plugins.netease_parser._get_group_history",
            new=AsyncMock(return_value=history),
        ):
            self.assertTrue(asyncio.run(self._match(event)))


class TestHistoryHelpers(unittest.TestCase):
    def test_history_before_trigger_by_message_id(self):
        history = [_history_item(i, 1000 + i, f"m{i}") for i in range(1, 21)]
        event = _make_group("x", message_id=12, time=1012)
        before = _history_before_trigger(history, event, limit=10)
        # message_id=12 → idx=11 → 之前 10 条 = m2..m11
        self.assertEqual([m["message_id"] for m in before], list(range(2, 12)))

    def test_history_before_trigger_by_time_fallback(self):
        history = [_history_item(i, 1000 + i, f"m{i}") for i in range(1, 21)]
        event = _make_group("x", message_id=515, time=1015)
        before = _history_before_trigger(history, event, limit=10)
        # message_id 不在历史 → 按 time>=1015 定位到 m15（items[14]）→ 之前 10 条 = m5..m14
        self.assertEqual([m["message_id"] for m in before], list(range(5, 15)))

    def test_history_before_trigger_all_when_not_found(self):
        history = [_history_item(i, 1000 + i, f"m{i}") for i in range(1, 21)]
        event = _make_group("x", message_id=999, time=99999)
        before = _history_before_trigger(history, event, limit=10)
        # 完全找不到 → 取最早的 10 条
        self.assertEqual([m["message_id"] for m in before], list(range(1, 11)))

    def test_history_event_extracts_song_id(self):
        item = _history_item(1, 1000, "https://music.163.com/song/33894312")
        ev = _history_event(item)
        ids = asyncio.run(extract_song_ids_from_event(ev))
        self.assertEqual(ids, ["33894312"])

    def test_history_event_card_extracts_song_id(self):
        card = {
            "app": "com.tencent.music.lua",
            "meta": {"music": {"jumpUrl": "https://music.163.com/song/33894312"}},
        }
        item = {
            "message_id": 1,
            "time": 1000,
            "message": [{"type": "json", "data": {"data": json.dumps(card)}}],
        }
        ev = _history_event(item)
        ids = asyncio.run(extract_song_ids_from_event(ev))
        self.assertEqual(ids, ["33894312"])


if __name__ == "__main__":
    unittest.main()
