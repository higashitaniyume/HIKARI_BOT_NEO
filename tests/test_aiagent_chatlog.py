"""aiagent 本地群消息记录器（chatlog）测试。"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent

from plugins.aiagent import chatlog
from plugins.aiagent.utils import format_timestamp

GROUP_ID = 10001
USER_ID = 20001


def _event(
    *,
    user_id: int = USER_ID,
    group_id: int = GROUP_ID,
    text: str | None = "你好",
    segments: list[dict[str, Any]] | None = None,
    self_id: int = 99999,
    nickname: str = "昵称",
    card: str = "",
) -> GroupMessageEvent:
    if segments is None:
        segments = [{"type": "text", "data": {"text": text or ""}}]
    return GroupMessageEvent(
        time=1700000000,
        self_id=self_id,
        post_type="message",
        message_type="group",
        sub_type="normal",
        message_id=1,
        group_id=group_id,
        user_id=user_id,
        raw_message=text or "",
        font=0,
        sender={"user_id": user_id, "nickname": nickname, "card": card, "role": "member"},
        message=segments,
    )


def _cfg(**chatlog_overrides: Any) -> dict[str, Any]:
    section: dict[str, Any] = {
        "enabled": True,
        "groups": [],
        "retention_days": 7,
        "max_total_mb": 200,
        "record_bot": False,
    }
    section.update(chatlog_overrides)
    return {"chatlog": section}


class ChatLogTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        for target, value in (
            ("CHATLOG_ROOT", self.root),
            ("_last_prune_at", 0.0),
        ):
            patcher = patch.object(chatlog, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _write_day(self, day: date, payload: str, group_id: int = GROUP_ID) -> Path:
        path = chatlog._day_path(group_id, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
        return path

    def _lines(self, group_id: int = GROUP_ID) -> list[dict[str, Any]]:
        path = chatlog._day_path(group_id, date.today())
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


class RecordTests(ChatLogTestCase):
    def test_records_text_message(self) -> None:
        self.assertTrue(chatlog.record_message(_event(text="今天天气不错"), _cfg()))
        entries = self._lines()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["u"], str(USER_ID))
        self.assertEqual(entries[0]["c"], "今天天气不错")
        self.assertEqual(entries[0]["n"], "昵称")
        self.assertEqual(entries[0]["t"], 1700000000)

    def test_card_wins_over_nickname(self) -> None:
        chatlog.record_message(_event(card="群名片"), _cfg())
        self.assertEqual(self._lines()[0]["n"], "群名片")

    def test_media_only_message_is_skipped(self) -> None:
        event = _event(text=None, segments=[{"type": "image", "data": {"file": "a.jpg"}}])
        self.assertFalse(chatlog.record_message(event, _cfg()))
        self.assertEqual(self._lines(), [])

    def test_mixed_message_keeps_placeholder(self) -> None:
        event = _event(
            text=None,
            segments=[
                {"type": "text", "data": {"text": "看这个"}},
                {"type": "image", "data": {"file": "a.jpg"}},
            ],
        )
        self.assertTrue(chatlog.record_message(event, _cfg()))
        self.assertEqual(self._lines()[0]["c"], "看这个[图片]")

    def test_disabled_records_nothing(self) -> None:
        self.assertFalse(chatlog.record_message(_event(), _cfg(enabled=False)))
        self.assertEqual(self._lines(), [])

    def test_group_allowlist(self) -> None:
        cfg = _cfg(groups=[str(GROUP_ID)])
        self.assertTrue(chatlog.record_message(_event(), cfg))
        self.assertFalse(chatlog.record_message(_event(group_id=77777), cfg))
        self.assertEqual(len(chatlog._all_day_files()), 1)

    def test_empty_allowlist_means_all_groups(self) -> None:
        cfg = _cfg(groups=[])
        self.assertTrue(chatlog.record_message(_event(group_id=77777), cfg))

    def test_bot_own_message_skipped_by_default(self) -> None:
        event = _event(user_id=99999, self_id=99999)
        self.assertFalse(chatlog.record_message(event, _cfg()))
        self.assertEqual(self._lines(), [])

    def test_bot_own_message_recorded_when_enabled(self) -> None:
        event = _event(user_id=99999, self_id=99999)
        self.assertTrue(chatlog.record_message(event, _cfg(record_bot=True)))
        self.assertEqual(len(self._lines()), 1)

    def test_write_failure_is_swallowed(self) -> None:
        blocker = self.root / "not-a-dir"
        blocker.write_text("x", encoding="utf-8")
        with patch.object(chatlog, "CHATLOG_ROOT", blocker):
            self.assertFalse(chatlog.record_message(_event(), _cfg()))

    def test_long_message_is_truncated(self) -> None:
        chatlog.record_message(_event(text="a" * 5000), _cfg())
        self.assertEqual(len(self._lines()[0]["c"]), chatlog.MAX_RECORD_CHARS)

    def test_appends_multiple_messages(self) -> None:
        for text in ("一", "二", "三"):
            chatlog.record_message(_event(text=text), _cfg())
        self.assertEqual([entry["c"] for entry in self._lines()], ["一", "二", "三"])


class ReadTests(ChatLogTestCase):
    def test_returns_chronological_order(self) -> None:
        for text in ("一", "二", "三"):
            chatlog.record_message(_event(text=text), _cfg())
        messages = chatlog.read_user_messages(_cfg(), GROUP_ID, USER_ID, limit=10)
        self.assertEqual([item["text"] for item in messages], ["一", "二", "三"])

    def test_limit_keeps_newest(self) -> None:
        for index in range(5):
            chatlog.record_message(_event(text=f"第{index}条"), _cfg())
        messages = chatlog.read_user_messages(_cfg(), GROUP_ID, USER_ID, limit=2)
        self.assertEqual([item["text"] for item in messages], ["第3条", "第4条"])

    def test_filters_by_user(self) -> None:
        chatlog.record_message(_event(text="我的"), _cfg())
        chatlog.record_message(_event(user_id=55555, text="别人的"), _cfg())
        messages = chatlog.read_user_messages(_cfg(), GROUP_ID, USER_ID, limit=10)
        self.assertEqual([item["text"] for item in messages], ["我的"])

    def test_filters_by_keyword(self) -> None:
        chatlog.record_message(_event(text="今天吃什么"), _cfg())
        chatlog.record_message(_event(text="在写代码"), _cfg())
        messages = chatlog.read_user_messages(_cfg(), GROUP_ID, USER_ID, limit=10, keyword="代码")
        self.assertEqual([item["text"] for item in messages], ["在写代码"])

    def test_isolated_per_group(self) -> None:
        chatlog.record_message(_event(text="本群"), _cfg())
        chatlog.record_message(_event(group_id=88888, text="别群"), _cfg())
        messages = chatlog.read_user_messages(_cfg(), GROUP_ID, USER_ID, limit=10)
        self.assertEqual([item["text"] for item in messages], ["本群"])

    def test_disabled_returns_nothing(self) -> None:
        chatlog.record_message(_event(text="记录"), _cfg())
        self.assertEqual(
            chatlog.read_user_messages(_cfg(enabled=False), GROUP_ID, USER_ID, limit=10), []
        )

    def test_missing_directory_returns_empty(self) -> None:
        self.assertEqual(
            chatlog.read_user_messages(_cfg(), 424242, USER_ID, limit=10), []
        )

    def test_malformed_lines_are_ignored(self) -> None:
        self._write_day(
            date.today(),
            "\n".join(
                [
                    "not json",
                    json.dumps({"t": 1700000000, "u": str(USER_ID), "c": "正常"}),
                    json.dumps({"t": 1700000001, "u": "abc"}),
                    json.dumps(["不是对象"]),
                ]
            )
            + "\n",
        )
        messages = chatlog.read_user_messages(_cfg(), GROUP_ID, USER_ID, limit=10)
        self.assertEqual([item["text"] for item in messages], ["正常"])

    def test_reads_across_day_files(self) -> None:
        self._write_day(
            date.today() - timedelta(days=1),
            json.dumps({"t": 1699999999, "u": str(USER_ID), "c": "昨天的"}) + "\n",
        )
        self._write_day(
            date.today(),
            json.dumps({"t": 1700000000, "u": str(USER_ID), "c": "今天的"}) + "\n",
        )
        messages = chatlog.read_user_messages(_cfg(), GROUP_ID, USER_ID, limit=10)
        self.assertEqual([item["text"] for item in messages], ["昨天的", "今天的"])

    def test_oversized_file_reads_only_the_tail(self) -> None:
        payload = "".join(
            json.dumps({"t": 1700000000 + index, "u": str(USER_ID), "n": "n", "c": f"第{index}条"})
            + "\n"
            for index in range(30)
        )
        self._write_day(date.today(), payload)

        with patch.object(chatlog, "MAX_READ_BYTES", 200):
            messages = chatlog.read_user_messages(_cfg(), GROUP_ID, USER_ID, limit=50)

        indices = [int(item["text"].removeprefix("第").removesuffix("条")) for item in messages]
        # 最新的发言一定读到，顺序仍是正序
        self.assertEqual(indices[-1], 29)
        self.assertEqual(indices, sorted(indices))
        # 只读了末尾一段：最早的几条不会出现
        self.assertNotIn(0, indices)
        self.assertLess(len(indices), 30)

    def test_long_text_is_capped(self) -> None:
        self._write_day(
            date.today(),
            json.dumps({"t": 1700000000, "u": str(USER_ID), "c": "b" * 900}) + "\n",
        )
        messages = chatlog.read_user_messages(_cfg(), GROUP_ID, USER_ID, limit=10)
        self.assertEqual(len(messages[0]["text"]), chatlog.MAX_READ_TEXT_CHARS)

    def test_formats_timestamp(self) -> None:
        chatlog.record_message(_event(text="时间"), _cfg())
        messages = chatlog.read_user_messages(_cfg(), GROUP_ID, USER_ID, limit=10)
        # 与本机时区无关：与 format_timestamp 的结果一致即可
        self.assertEqual(messages[0]["time"], format_timestamp(1700000000))
        self.assertTrue(messages[0]["time"])


class PruneTests(ChatLogTestCase):
    def test_removes_expired_days(self) -> None:
        old = self._write_day(date.today() - timedelta(days=10), "{}\n")
        fresh = self._write_day(date.today() - timedelta(days=3), "{}\n")
        removed = chatlog.prune(_cfg(retention_days=7), force=True)
        self.assertEqual(removed, 1)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_empty_group_dirs_are_removed(self) -> None:
        old = self._write_day(date.today() - timedelta(days=30), "{}\n")
        group_dir = old.parent
        chatlog.prune(_cfg(retention_days=1), force=True)
        self.assertFalse(group_dir.exists())

    def test_total_size_cap_deletes_oldest_first(self) -> None:
        old = self._write_day(date.today() - timedelta(days=1), "x" * 1_100_000)
        fresh = self._write_day(date.today(), "small\n")
        removed = chatlog.prune(_cfg(max_total_mb=1), force=True)
        self.assertEqual(removed, 1)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_hourly_gate_skips_until_forced(self) -> None:
        old = self._write_day(date.today() - timedelta(days=10), "{}\n")
        with patch.object(chatlog, "_last_prune_at", time.monotonic()):
            self.assertEqual(chatlog.prune(_cfg(retention_days=1)), 0)
            self.assertTrue(old.exists())
            self.assertEqual(chatlog.prune(_cfg(retention_days=1), force=True), 1)
        self.assertFalse(old.exists())

    def test_no_files_is_noop(self) -> None:
        self.assertEqual(chatlog.prune(_cfg(), force=True), 0)


class ConfigTests(unittest.TestCase):
    def test_defaults(self) -> None:
        self.assertTrue(chatlog.enabled({}))
        self.assertFalse(chatlog.record_bot({}))
        self.assertEqual(chatlog.retention_days({}), 7)
        self.assertEqual(chatlog.max_total_mb({}), 200)
        self.assertTrue(chatlog.allowed_group({}, 123))

    def test_values_are_clamped(self) -> None:
        cfg = _cfg(retention_days=9999, max_total_mb=0)
        self.assertEqual(chatlog.retention_days(cfg), 365)
        self.assertEqual(chatlog.max_total_mb(cfg), 1)


class MatcherTests(ChatLogTestCase):
    def test_matcher_is_passive_and_runs_before_ai(self) -> None:
        self.assertEqual(chatlog.chatlog_recorder.priority, 80)
        self.assertFalse(chatlog.chatlog_recorder.block)

    def test_handler_records_message(self) -> None:
        import asyncio

        with patch.object(chatlog, "get_config_for_event", return_value=_cfg()):
            asyncio.run(chatlog._handle_chatlog_record(_event(text="经过 matcher")))
        self.assertEqual([entry["c"] for entry in self._lines()], ["经过 matcher"])

    def test_handler_ignores_private_message(self) -> None:
        import asyncio

        private = PrivateMessageEvent(
            time=1700000000,
            self_id=99999,
            post_type="message",
            message_type="private",
            sub_type="friend",
            message_id=2,
            user_id=USER_ID,
            raw_message="私聊消息",
            font=0,
            sender={"user_id": USER_ID, "nickname": "昵称"},
            message=[{"type": "text", "data": {"text": "私聊消息"}}],
        )
        with patch.object(chatlog, "get_config_for_event", return_value=_cfg()) as get_cfg:
            asyncio.run(chatlog._handle_chatlog_record(private))

        # 私聊既不记录，也不去读配置
        self.assertEqual(self._lines(), [])
        get_cfg.assert_not_called()

    def test_handler_never_raises(self) -> None:
        import asyncio

        with patch.object(
            chatlog, "get_config_for_event", side_effect=RuntimeError("config boom")
        ):
            asyncio.run(chatlog._handle_chatlog_record(_event()))
        self.assertEqual(self._lines(), [])

    def test_handler_survives_record_failure(self) -> None:
        import asyncio

        with (
            patch.object(chatlog, "get_config_for_event", return_value=_cfg()),
            patch.object(chatlog, "record_message", side_effect=OSError("disk full")),
        ):
            asyncio.run(chatlog._handle_chatlog_record(_event()))


if __name__ == "__main__":
    unittest.main()
