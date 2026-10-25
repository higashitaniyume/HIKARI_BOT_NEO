from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import nonebot

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="nonebot.drivers.none:Driver")

import plugins.qzone as qzone
import plugins.qzone.comment_reply as comment_reply
from plugins.qzone import service
from plugins.qzone.config import normalize_comment_reply

SELF_ID = "3946388948"


def _config(section: dict, *, state_path: str) -> dict:
    merged = normalize_comment_reply({**section, "state_path": state_path})
    return {"enabled": True, "timeout": 20, "comment_reply": merged}


def _post(tid: str = "tid1", content: str = "今天的说说") -> service.MoodItem:
    return service.MoodItem(
        tid=tid, uin=SELF_ID, name="HIKARI", content=content, created_at="今天 12:00", comment_count=1
    )


def _comment(uin: str = "10001", nickname: str = "小明", content: str = "好看") -> service.CommentItem:
    return service.CommentItem(comment_id="1", uin=uin, nickname=nickname, content=content)


def _thread(*, replied: bool = False, **kwargs) -> service.CommentThread:
    replies = (
        (service.CommentItem(comment_id="1", uin=SELF_ID, nickname="我", content="回了", depth=1),)
        if replied
        else ()
    )
    return service.CommentThread(comment=_comment(**kwargs), replies=replies)


class _Bot:
    self_id = SELF_ID


class CheckOnceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._state = str(Path(self._tmp.name) / "comment_state.json")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_state(self, data: dict) -> None:
        Path(self._state).write_text(json.dumps(data), encoding="utf-8")

    async def test_first_run_only_baselines(self) -> None:
        cfg = _config({"enabled": True}, state_path=self._state)
        reply = AsyncMock()
        with patch.object(comment_reply, "get_config", return_value=cfg), patch.object(
            comment_reply, "_pick_bot", return_value=_Bot()
        ), patch.object(service, "fetch_moods", AsyncMock(return_value=[_post()])), patch.object(
            service, "fetch_comment_threads", AsyncMock(return_value=[_thread()])
        ), patch.object(service, "reply_comment", reply):
            result = await comment_reply.check_once()

        self.assertEqual(result["baseline"], 1)
        reply.assert_not_awaited()
        state = comment_reply.load_state(Path(self._state))
        self.assertTrue(state["baseline_done"])
        self.assertEqual(len(state["seen"]), 1)

    async def test_replies_to_new_comment(self) -> None:
        self._write_state({"baseline_done": True, "seen": {}, "daily": {}})
        cfg = _config({"enabled": True, "mode": "template", "template": "谢谢{name}喵"}, state_path=self._state)
        reply = AsyncMock()
        with patch.object(comment_reply, "get_config", return_value=cfg), patch.object(
            comment_reply, "_pick_bot", return_value=_Bot()
        ), patch.object(service, "fetch_moods", AsyncMock(return_value=[_post()])), patch.object(
            service, "fetch_comment_threads", AsyncMock(return_value=[_thread()])
        ), patch.object(service, "reply_comment", reply), patch.object(
            comment_reply.asyncio, "sleep", AsyncMock()
        ):
            result = await comment_reply.check_once()

        self.assertEqual(result["replied"], 1)
        reply.assert_awaited_once()
        text = reply.await_args.args[4]
        self.assertEqual(text, "谢谢小明喵")
        state = comment_reply.load_state(Path(self._state))
        self.assertEqual(state["daily"][list(state["daily"])[0]], 1)

    async def test_skips_comments_already_replied(self) -> None:
        self._write_state({"baseline_done": True, "seen": {}, "daily": {}})
        cfg = _config({"enabled": True}, state_path=self._state)
        reply = AsyncMock()
        with patch.object(comment_reply, "get_config", return_value=cfg), patch.object(
            comment_reply, "_pick_bot", return_value=_Bot()
        ), patch.object(service, "fetch_moods", AsyncMock(return_value=[_post()])), patch.object(
            service, "fetch_comment_threads", AsyncMock(return_value=[_thread(replied=True)])
        ), patch.object(service, "reply_comment", reply):
            result = await comment_reply.check_once()

        self.assertEqual(result["replied"], 0)
        reply.assert_not_awaited()

    async def test_skips_seen_comments(self) -> None:
        self._write_state({"baseline_done": True, "seen": {"tid1:1:10001": "2026-10-25 10:00"}, "daily": {}})
        cfg = _config({"enabled": True}, state_path=self._state)
        reply = AsyncMock()
        with patch.object(comment_reply, "get_config", return_value=cfg), patch.object(
            comment_reply, "_pick_bot", return_value=_Bot()
        ), patch.object(service, "fetch_moods", AsyncMock(return_value=[_post()])), patch.object(
            service, "fetch_comment_threads", AsyncMock(return_value=[_thread()])
        ), patch.object(service, "reply_comment", reply):
            result = await comment_reply.check_once()

        self.assertEqual(result["replied"], 0)
        reply.assert_not_awaited()

    async def test_daily_limit_blocks_replies(self) -> None:
        self._write_state({"baseline_done": True, "seen": {}, "daily": {}})
        cfg = _config({"enabled": True, "daily_limit": 0}, state_path=self._state)
        reply = AsyncMock()
        with patch.object(comment_reply, "get_config", return_value=cfg), patch.object(
            comment_reply, "_pick_bot", return_value=_Bot()
        ), patch.object(service, "fetch_moods", AsyncMock(return_value=[_post()])), patch.object(
            service, "fetch_comment_threads", AsyncMock(return_value=[_thread()])
        ), patch.object(service, "reply_comment", reply):
            result = await comment_reply.check_once()

        self.assertEqual(result["replied"], 0)
        reply.assert_not_awaited()

    async def test_skips_own_comment_and_disabled(self) -> None:
        self._write_state({"baseline_done": True, "seen": {}, "daily": {}})
        cfg = _config({"enabled": True}, state_path=self._state)
        with patch.object(comment_reply, "get_config", return_value=cfg), patch.object(
            comment_reply, "_pick_bot", return_value=_Bot()
        ), patch.object(service, "fetch_moods", AsyncMock(return_value=[_post()])), patch.object(
            service, "fetch_comment_threads", AsyncMock(return_value=[_thread(uin=SELF_ID)])
        ), patch.object(service, "reply_comment", AsyncMock()) as reply:
            result = await comment_reply.check_once()
        self.assertEqual(result["replied"], 0)
        reply.assert_not_awaited()

        disabled = _config({"enabled": False}, state_path=self._state)
        with patch.object(comment_reply, "get_config", return_value=disabled):
            self.assertEqual(await comment_reply.check_once(), {"skipped": "disabled"})


class ComposeReplyTests(unittest.IsolatedAsyncioTestCase):
    async def test_template_mode(self) -> None:
        section = normalize_comment_reply(
            {"mode": "template", "template": "谢谢{name}，{content} 收到啦"}
        )
        text = await comment_reply.compose_reply(_post(), _comment(content="好看"), section)
        self.assertEqual(text, "谢谢小明，好看 收到啦")

    async def test_ai_failure_falls_back_to_template(self) -> None:
        section = normalize_comment_reply({"mode": "ai", "template": "谢谢{name}喵"})
        with patch.object(comment_reply, "_generate_with_ai", AsyncMock(side_effect=RuntimeError("boom"))):
            text = await comment_reply.compose_reply(_post(), _comment(), section)
        self.assertEqual(text, "谢谢小明喵")

    async def test_ai_text_is_cleaned(self) -> None:
        section = normalize_comment_reply({"mode": "ai", "ai": {"max_chars": 20}})
        with patch.object(
            comment_reply, "_generate_with_ai", AsyncMock(return_value="谢谢喵（摇尾巴）")
        ):
            text = await comment_reply.compose_reply(_post(), _comment(), section)
        self.assertEqual(text, "谢谢喵（摇尾巴）")


class StatusTests(unittest.TestCase):
    def test_status_snapshot_reads_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(
                json.dumps(
                    {
                        "baseline_done": True,
                        "seen": {"a": "x", "b": "y"},
                        "daily": {comment_reply.date.today().isoformat(): 3},
                        "last_run": "2026-10-25 18:00",
                        "last_result": {"replied": 1},
                    }
                ),
                encoding="utf-8",
            )
            cfg = _config({"enabled": True, "interval_minutes": 30}, state_path=str(path))
            with patch.object(comment_reply, "get_config", return_value=cfg):
                snapshot = comment_reply.status_snapshot()

        self.assertTrue(snapshot["baseline_done"])
        self.assertEqual(snapshot["seen"], 2)
        self.assertEqual(snapshot["replied_today"], 3)
        self.assertEqual(snapshot["last_run"], "2026-10-25 18:00")
        self.assertEqual(snapshot["interval_minutes"], 30)


class CheckCommandTests(unittest.IsolatedAsyncioTestCase):
    def _ctx(self) -> object:
        class Ctx:
            args = ""
            bot = _Bot()
            event = Mock()

            def __init__(self) -> None:
                self.sent: list[str] = []

            async def send(self, message: object) -> None:
                self.sent.append(str(message))

        return Ctx()

    async def test_command_reports_counts(self) -> None:
        ctx = self._ctx()
        cfg = {"enabled": True, "comment_reply": {}}
        with patch.object(qzone, "get_config", return_value=cfg), patch.object(
            qzone.comment_reply, "check_once", AsyncMock(return_value={"replied": 1, "pending": 2, "failed": 0})
        ):
            await qzone.handle_check_comments(ctx)
        self.assertIn("1", ctx.sent[-1])

    async def test_command_reports_baseline(self) -> None:
        ctx = self._ctx()
        cfg = {"enabled": True, "comment_reply": {}}
        with patch.object(qzone, "get_config", return_value=cfg), patch.object(
            qzone.comment_reply, "check_once", AsyncMock(return_value={"baseline": 4})
        ):
            await qzone.handle_check_comments(ctx)
        self.assertIn("4", ctx.sent[-1])


if __name__ == "__main__":
    unittest.main()
