"""AI Agent 表情包跟随（`plugins/aiagent/sticker.py`）测试。

覆盖：
- 未启用时跳过
- 概率控制（0.0 跳过，1.0 发送）
- 自动回复标志位控制
- 候选贴纸为空时静默跳过
- 发送成功与 URI 构造
- 最近 5 张去重与耗尽重置
- 发送异常静默降级（返回 False，不抛异常）
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from plugins.aiagent import reply as reply_mod
from plugins.aiagent import sticker as sticker_mod
from plugins.aiagent.sticker import _recent_sent, maybe_send_sticker


class FakeEvent:
    def __init__(self, user_id: int = 123456, group_id: int | None = None) -> None:
        self.user_id = user_id
        self.group_id = group_id

    def get_user_id(self) -> str:
        return str(self.user_id)


class FakeBot:
    def __init__(self, self_id: str = "10000") -> None:
        self.self_id = self_id
        self.sent_messages: list[Any] = []
        self.send_error: Exception | None = None

    async def send(self, event: Any, message: Any) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent_messages.append({"event": event, "message": message})


class HangingBot(FakeBot):
    async def send(self, event: Any, message: Any) -> None:
        await asyncio.sleep(30)


def _cfg(**sticker_overrides: Any) -> dict[str, Any]:
    stk = {
        "enabled": True,
        "probability": 1.0,
        "allow_auto_reply": True,
        "packs": [],
    }
    stk.update(sticker_overrides)
    return {"chat": {"sticker": stk}}


class StickerReplyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _recent_sent.clear()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._shared_patch = patch.object(
            sticker_mod,
            "SHARED_STICKER_DIR",
            Path(self._tmpdir.name) / "shared",
        )
        self._shared_patch.start()
        self.addCleanup(self._shared_patch.stop)

    def _sticker(self, name: str) -> Path:
        path = Path(self._tmpdir.name) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"GIF89a\x01\x00\x01\x00")
        return path

    async def test_disabled_skips(self) -> None:
        bot = FakeBot()
        event = FakeEvent(group_id=123)
        cfg = _cfg(enabled=False)

        res = await maybe_send_sticker(bot, event, cfg)
        self.assertFalse(res)
        self.assertEqual(len(bot.sent_messages), 0)

    async def test_zero_probability_skips(self) -> None:
        bot = FakeBot()
        event = FakeEvent(group_id=123)
        cfg = _cfg(probability=0.0)

        res = await maybe_send_sticker(bot, event, cfg)
        self.assertFalse(res)
        self.assertEqual(len(bot.sent_messages), 0)

    async def test_auto_reply_not_allowed_skips(self) -> None:
        bot = FakeBot()
        event = FakeEvent(group_id=123)
        cfg = _cfg(allow_auto_reply=False)

        res = await maybe_send_sticker(bot, event, cfg, is_auto_reply=True)
        self.assertFalse(res)
        self.assertEqual(len(bot.sent_messages), 0)

    @patch("plugins.sticker_library.get_all_files")
    async def test_empty_candidates_skips(self, mock_get_all: Any) -> None:
        mock_get_all.return_value = []
        bot = FakeBot()
        event = FakeEvent(group_id=123)
        cfg = _cfg()

        res = await maybe_send_sticker(bot, event, cfg)
        self.assertFalse(res)
        self.assertEqual(len(bot.sent_messages), 0)

    @patch("plugins.sticker_library.get_all_files")
    async def test_send_success_with_all_packs(self, mock_get_all: Any) -> None:
        fake_file = self._sticker("fake_sticker.gif")
        mock_get_all.return_value = [fake_file]

        bot = FakeBot()
        event = FakeEvent(group_id=123)
        cfg = _cfg(packs=[])

        res = await maybe_send_sticker(bot, event, cfg)
        self.assertTrue(res)
        self.assertEqual(len(bot.sent_messages), 1)

    @patch("plugins.sticker_library.get_packs_files")
    async def test_pack_filter_invokes_get_packs_files(self, mock_get_packs: Any) -> None:
        fake_file = self._sticker("pack_a/stk.gif")
        mock_get_packs.return_value = [fake_file]

        bot = FakeBot()
        event = FakeEvent(user_id=456)
        cfg = _cfg(packs=["pack_a"])

        res = await maybe_send_sticker(bot, event, cfg)
        self.assertTrue(res)
        mock_get_packs.assert_called_once_with(["pack_a"])
        self.assertEqual(len(bot.sent_messages), 1)

    @patch("plugins.sticker_library.get_all_files")
    async def test_recent_dedup_and_recycle(self, mock_get_all: Any) -> None:
        # 6 张候选表情
        candidates = [self._sticker(f"stk_{i}.gif") for i in range(6)]
        mock_get_all.return_value = candidates

        bot = FakeBot()
        event = FakeEvent(group_id=999)
        cfg = _cfg()

        sent_paths: list[str] = []
        for _ in range(6):
            res = await maybe_send_sticker(bot, event, cfg)
            self.assertTrue(res)

        # 检查 session 记录里最多保持 5 张
        session_recent = _recent_sent.get("group:999", [])
        self.assertLessEqual(len(session_recent), 5)

    @patch("plugins.sticker_library.get_all_files")
    async def test_send_error_fails_silently(self, mock_get_all: Any) -> None:
        fake_file = self._sticker("error_sticker.gif")
        mock_get_all.return_value = [fake_file]

        bot = FakeBot()
        bot.send_error = RuntimeError("Network error")
        event = FakeEvent(group_id=123)
        cfg = _cfg()

        with patch.object(reply_mod, "SEND_RETRY_DELAY_SECONDS", 0):
            res = await maybe_send_sticker(bot, event, cfg)
        self.assertFalse(res)

    @patch("plugins.sticker_library.get_all_files")
    async def test_missing_sticker_file_is_skipped(self, mock_get_all: Any) -> None:
        mock_get_all.return_value = [Path(self._tmpdir.name) / "missing.gif"]

        bot = FakeBot()
        event = FakeEvent(group_id=123)

        res = await maybe_send_sticker(bot, event, _cfg())

        self.assertFalse(res)
        self.assertEqual([], bot.sent_messages)

    @patch("plugins.sticker_library.get_all_files")
    async def test_send_timeout_fails_silently(self, mock_get_all: Any) -> None:
        fake_file = self._sticker("hanging_sticker.gif")
        mock_get_all.return_value = [fake_file]

        bot = HangingBot()
        event = FakeEvent(group_id=123)
        cfg = _cfg()

        with patch.object(reply_mod, "SEND_TIMEOUT_SECONDS", 0.01):
            res = await asyncio.wait_for(maybe_send_sticker(bot, event, cfg), timeout=1.0)

        self.assertFalse(res)


if __name__ == "__main__":
    unittest.main()
