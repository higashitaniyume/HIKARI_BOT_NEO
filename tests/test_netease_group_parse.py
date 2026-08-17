"""Netease group manual-parse trigger tests — @bot + link / @bot + 引用卡片."""

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
from nonebot.adapters.onebot.v11.event import Reply, Sender

from plugins.netease_parser import AutoNeteaseHandler, _history_event
from plugins.netease_parser.parser import classify_links


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
    at_mid_message: bool = False,
    message_id: int = 500,
    time: int = 2000,
    reply_id: str = "",
) -> GroupMessageEvent:
    """构造群聊事件。

    at_self=True：模拟适配器处理后的"@开头"场景 —— to_me=True 且 at 段
    已被适配器从 message 中移除（_check_at_me 的行为）。
    at_mid_message=True：@ 在消息中间 —— to_me=False 但 at 段保留。
    reply_id：模拟引用回复某条消息（reply 段）。
    """
    segments = []
    if reply_id:
        segments.append(MessageSegment.reply(int(reply_id)))
    if at_mid_message:
        segments.append(MessageSegment.at("10000"))
    if text:
        segments.append(MessageSegment.text(text))
    msg = Message(segments)
    return GroupMessageEvent(
        time=time, self_id="10000", post_type="message", message_type="group",
        sub_type="normal", group_id=group_id, user_id=10001, message_id=message_id,
        message=msg, raw_message=str(msg), font=0, sender=Sender(user_id=10001),
        to_me=at_self,
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


def _set_processed_reply(
    event: GroupMessageEvent,
    message: Message,
    *,
    reply_id: int = 999,
    sender_id: int = 10002,
) -> GroupMessageEvent:
    """模拟 NoneBot _check_reply 已移除 reply 段并写入 event.reply。"""
    event.reply = Reply(
        time=1000,
        message_type="group",
        message_id=reply_id,
        real_id=reply_id,
        sender=Sender(user_id=sender_id),
        message=message,
    )
    return event


class TestGroupParseTrigger(unittest.TestCase):
    async def _match(self, event, cfg=None):
        handler = AutoNeteaseHandler()
        with patch(
            "plugins.netease_parser.get_config",
            return_value=cfg if cfg is not None else _cfg(),
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

    def test_manual_group_at_with_link_matches(self):
        # @ 开头（to_me=True）+ 自身带链接 → 解析
        event = _make_group("https://music.163.com/song/33894312")
        self.assertTrue(asyncio.run(self._match(event, _cfg(auto_enable=False))))

    def test_manual_group_at_mid_message_matches(self):
        # @ 在消息中间：to_me=False 但 at 段保留 → 段遍历兜底命中
        event = _make_group(
            "https://music.163.com/song/33894312",
            at_self=False, at_mid_message=True,
        )
        self.assertTrue(asyncio.run(self._match(event, _cfg(auto_enable=False))))

    def test_manual_group_at_no_link_no_reply_not_matched(self):
        # @bot 但既无链接也无引用 → 不解析（交给 AI 等后续处理）
        event = _make_group("帮我解析")
        self.assertFalse(asyncio.run(self._match(event, _cfg(auto_enable=False))))

    def test_manual_group_at_with_processed_reply_card_matches(self):
        # 线上形态：reply 段已被 NoneBot 删除，只剩 event.reply + to_me=True。
        card = {
            "app": "com.tencent.music.lua",
            "meta": {"music": {"jumpUrl": "https://music.163.com/song/33894312"}},
        }
        ref_message = Message([
            MessageSegment(type="json", data={"data": json.dumps(card)}),
        ])
        event = _set_processed_reply(_make_group(""), ref_message)
        self.assertTrue(asyncio.run(self._match(event, _cfg(auto_enable=False))))

    def test_manual_group_at_with_unrelated_processed_reply_not_matched(self):
        event = _set_processed_reply(_make_group(""), Message("普通消息"))
        self.assertFalse(asyncio.run(self._match(event, _cfg(auto_enable=False))))


class TestGroupHandle(unittest.TestCase):
    def _ref_event(self, text: str) -> SimpleNamespace:
        msg = Message(text)
        return SimpleNamespace(message=msg, get_message=lambda: msg)

    def test_reply_referenced_song_enqueued(self):
        """NoneBot 已解析 event.reply 时，直接复用引用内容，不重复 get_msg。"""
        event = _set_processed_reply(
            _make_group(""),
            Message("https://music.163.com/song/33894312"),
        )
        with patch(
            "plugins.netease_parser.get_config", return_value=_cfg(),
        ), patch(
            "plugins.netease_parser._enqueue_parse_jobs", new=AsyncMock(),
        ) as enqueue:
            bot = AsyncMock()
            asyncio.run(AutoNeteaseHandler().handle(bot, event))
            bot.call_api.assert_not_awaited()
            enqueue.assert_awaited_once()
            self.assertEqual(enqueue.call_args.args[2], ["33894312"])
            bot.send.assert_not_awaited()

    def test_reply_referenced_album_prompts_private(self):
        """@bot + 引用卡片（被引用消息含专辑链接）→ 提示仅支持私聊。"""
        event = _make_group("解析", reply_id="999")
        ref = self._ref_event("https://music.163.com/album?id=379731879")
        with patch(
            "plugins.netease_parser.get_config", return_value=_cfg(),
        ), patch(
            "plugins.netease_parser._fetch_referenced_message",
            new=AsyncMock(return_value=ref),
        ), patch(
            "plugins.netease_parser._enqueue_album_parse_job", new=AsyncMock(),
        ) as album_enqueue:
            bot = AsyncMock()
            asyncio.run(AutoNeteaseHandler().handle(bot, event))
            bot.send.assert_awaited_once()
            album_enqueue.assert_not_awaited()

    def test_group_direct_album_prompts_private(self):
        """群聊 @bot 直接带专辑链接 → 提示仅支持私聊。"""
        event = _make_group("https://music.163.com/album?id=379731879")
        with patch(
            "plugins.netease_parser.get_config", return_value=_cfg(),
        ), patch(
            "plugins.netease_parser._enqueue_album_parse_job", new=AsyncMock(),
        ) as album_enqueue:
            bot = AsyncMock()
            asyncio.run(AutoNeteaseHandler().handle(bot, event))
            bot.send.assert_awaited_once()
            album_enqueue.assert_not_awaited()

    def test_group_direct_song_enqueued(self):
        """群聊 @bot 直接带单曲链接 → 入队解析。"""
        event = _make_group("https://music.163.com/song/33894312")
        with patch(
            "plugins.netease_parser.get_config", return_value=_cfg(),
        ), patch(
            "plugins.netease_parser._enqueue_parse_jobs", new=AsyncMock(),
        ) as enqueue:
            bot = AsyncMock()
            asyncio.run(AutoNeteaseHandler().handle(bot, event))
            enqueue.assert_awaited_once()
            self.assertEqual(enqueue.call_args.args[2], ["33894312"])
            bot.send.assert_not_awaited()


class TestHistoryEventHelpers(unittest.TestCase):
    def test_history_event_extracts_song_id(self):
        item = _history_item(1, 1000, "https://music.163.com/song/33894312")
        ev = _history_event(item)
        links = asyncio.run(classify_links(ev))
        self.assertEqual(links.song_ids, ["33894312"])

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
        links = asyncio.run(classify_links(ev))
        self.assertEqual(links.song_ids, ["33894312"])


if __name__ == "__main__":
    unittest.main()
