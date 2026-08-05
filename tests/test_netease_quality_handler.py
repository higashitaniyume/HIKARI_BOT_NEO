"""Netease quality handler tests — preference declaration & reply reconvert."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from nonebot.adapters.onebot.v11 import Message

from plugins.netease_parser import (
    NeteaseQualityHandler,
    _get_reply_message_id,
    _plain_text,
)
from plugins.netease_parser import prefs


def _make_event(text: str = "mp3", reply_id: str = ""):
    msg_text = f"[CQ:reply,id={reply_id}]{text}" if reply_id else text
    message = Message(msg_text)
    return SimpleNamespace(
        message=message,
        get_user_id=lambda: "10001",
        get_message=lambda: message,
    )


class TestReplyHelpers(unittest.TestCase):
    def test_plain_text_skips_reply_segment(self):
        event = _make_event("mp3", "123")
        self.assertEqual(_plain_text(event), "mp3")

    def test_get_reply_message_id(self):
        event = _make_event("mp3", "123")
        self.assertEqual(_get_reply_message_id(event), "123")

    def test_get_reply_message_id_empty(self):
        event = _make_event("mp3")
        self.assertEqual(_get_reply_message_id(event), "")


class TestQualityHandler(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_path = prefs.PREFS_PATH
        prefs.PREFS_PATH = Path(self._tmp.name) / "netease_prefs.json"
        prefs._recent.clear()

    def tearDown(self):
        prefs.PREFS_PATH = self._orig_path
        prefs._recent.clear()
        self._tmp.cleanup()

    def test_match_reply_mp3(self):
        event = _make_event("mp3", "123")
        self.assertTrue(asyncio.run(NeteaseQualityHandler().match(event, "")))

    def test_match_plain_mp3(self):
        event = _make_event("我要mp3")
        self.assertTrue(asyncio.run(NeteaseQualityHandler().match(event, "")))

    def test_match_plain_flac(self):
        event = _make_event("用flac")
        self.assertTrue(asyncio.run(NeteaseQualityHandler().match(event, "")))

    def test_match_plain_without_keyword(self):
        event = _make_event("你好")
        self.assertFalse(asyncio.run(NeteaseQualityHandler().match(event, "")))

    def test_handle_reply_finds_record_and_reconverts(self):
        event = _make_event("mp3", "111")
        prefs.record_send(
            "10001", item_type="song", item_id="42", title="歌",
            quality="flac", message_ids=["111"],
        )
        with patch(
            "plugins.netease_parser._enqueue_reconvert", new=AsyncMock(),
        ) as reconvert, patch(
            "plugins.netease_parser.get_config",
            return_value={"quality_switch": True},
        ):
            asyncio.run(NeteaseQualityHandler().handle(AsyncMock(), event))
            reconvert.assert_awaited_once()
            rec = reconvert.call_args.args[2]
            self.assertEqual(rec.item_id, "42")
            self.assertEqual(reconvert.call_args.args[3], "mp3")

    def test_handle_reply_same_quality(self):
        event = _make_event("mp3", "111")
        prefs.record_send(
            "10001", item_type="song", item_id="42", title="歌",
            quality="mp3", message_ids=["111"],
        )
        bot = AsyncMock()
        with patch(
            "plugins.netease_parser._enqueue_reconvert", new=AsyncMock(),
        ) as reconvert:
            asyncio.run(NeteaseQualityHandler().handle(bot, event))
            reconvert.assert_not_awaited()
            bot.send.assert_awaited_once()

    def test_handle_reply_not_found_sets_preference(self):
        event = _make_event("mp3", "999")
        bot = AsyncMock()
        with patch(
            "plugins.netease_parser._enqueue_reconvert", new=AsyncMock(),
        ) as reconvert:
            asyncio.run(NeteaseQualityHandler().handle(bot, event))
            reconvert.assert_not_awaited()
            self.assertEqual(prefs.get_user_quality("10001"), "mp3")

    def test_handle_plain_sets_preference(self):
        event = _make_event("我要mp3")
        bot = AsyncMock()
        asyncio.run(NeteaseQualityHandler().handle(bot, event))
        self.assertEqual(prefs.get_user_quality("10001"), "mp3")
        bot.send.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
