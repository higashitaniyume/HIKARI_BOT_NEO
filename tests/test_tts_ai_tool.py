from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import nonebot

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="nonebot.drivers.none:Driver")

from core.ai_tool_registry import AIToolContext, get_ai_tool

import plugins.tts_speaker.ai_tool as tts_ai_tool


def _fake_event(user_id: str = "42") -> SimpleNamespace:
    return SimpleNamespace(get_user_id=lambda: user_id)


class TTSToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        tts_ai_tool._last_used_at.clear()
        self._tmp = tempfile.TemporaryDirectory()
        self._audio = Path(self._tmp.name) / "voice.mp3"
        self._audio.write_bytes(b"fake-mp3")
        self._config = {
            "enabled": True,
            "voices": [
                {"name": "永雏塔菲", "reference_id": "a"},
                {"name": "电棍", "reference_id": "b"},
            ],
            "selected_voice": "永雏塔菲",
            "cache_dir": self._tmp.name,
            "cache_ttl_minutes": 60,
            "ai_tool": {
                "max_chars": 80,
                "cooldown_seconds": 10,
                "allow_auto_reply": False,
            },
        }
        self._bot = AsyncMock()

    def tearDown(self) -> None:
        tts_ai_tool._last_used_at.clear()
        self._tmp.cleanup()

    def _context(self, *, auto: bool = False) -> AIToolContext:
        return AIToolContext(bot=self._bot, event=_fake_event(), auto=auto)

    async def test_tool_contract_is_registered_as_opt_in_side_effect(self) -> None:
        spec = get_ai_tool(tts_ai_tool.TOOL_NAME)
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual(spec.plugin_name, "tts_speaker")
        self.assertFalse(spec.readonly)
        self.assertFalse(spec.enabled_by_default)
        self.assertEqual(set(spec.parameters.get("required") or []), {"text"})

    async def test_send_voice_generates_and_sends_record(self) -> None:
        render = AsyncMock(return_value=self._audio)
        with (
            patch.object(tts_ai_tool, "get_config", Mock(return_value=self._config)),
            patch.object(tts_ai_tool, "_render_fish_tts", render),
        ):
            result = await tts_ai_tool.ai_send_voice(
                self._context(),
                {"text": "  晚上好呀  "},
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["sent"])
        self.assertEqual(result["voice"], "永雏塔菲")
        self.assertEqual(result["chars"], len("晚上好呀"))
        render.assert_awaited_once_with(
            "晚上好呀",
            self._config,
            Path(self._tmp.name),
            "永雏塔菲",
        )
        self._bot.send.assert_awaited_once()
        message = self._bot.send.await_args.args[1]
        self.assertEqual(message[0].type, "record")
        self.assertTrue(str(message[0].data["file"]).startswith("file:"))

    async def test_explicit_voice_is_used(self) -> None:
        render = AsyncMock(return_value=self._audio)
        with (
            patch.object(tts_ai_tool, "get_config", Mock(return_value=self._config)),
            patch.object(tts_ai_tool, "_render_fish_tts", render),
        ):
            result = await tts_ai_tool.ai_send_voice(
                self._context(),
                {"text": "换个人说话", "voice": "电棍"},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["voice"], "电棍")
        self.assertEqual(render.await_args.args[3], "电棍")

    async def test_unknown_voice_returns_available_names(self) -> None:
        render = AsyncMock(return_value=self._audio)
        with (
            patch.object(tts_ai_tool, "get_config", Mock(return_value=self._config)),
            patch.object(tts_ai_tool, "_render_fish_tts", render),
        ):
            result = await tts_ai_tool.ai_send_voice(
                self._context(),
                {"text": "你好", "voice": "不存在"},
            )

        self.assertIn("error", result)
        self.assertEqual(result["available_voices"], ["永雏塔菲", "电棍"])
        render.assert_not_awaited()
        self._bot.send.assert_not_awaited()

    async def test_text_over_limit_is_rejected(self) -> None:
        render = AsyncMock(return_value=self._audio)
        with (
            patch.object(tts_ai_tool, "get_config", Mock(return_value=self._config)),
            patch.object(tts_ai_tool, "_render_fish_tts", render),
        ):
            result = await tts_ai_tool.ai_send_voice(
                self._context(),
                {"text": "说" * 81},
            )

        self.assertIn("too long", result["error"])
        self.assertEqual(result["max_chars"], 80)
        render.assert_not_awaited()

    async def test_auto_reply_is_blocked_by_default(self) -> None:
        render = AsyncMock(return_value=self._audio)
        with (
            patch.object(tts_ai_tool, "get_config", Mock(return_value=self._config)),
            patch.object(tts_ai_tool, "_render_fish_tts", render),
        ):
            result = await tts_ai_tool.ai_send_voice(
                self._context(auto=True),
                {"text": "我自己插一句"},
            )

        self.assertIn("automatic replies", result["error"])
        render.assert_not_awaited()
        self._bot.send.assert_not_awaited()

    async def test_auto_reply_can_be_allowed(self) -> None:
        cfg = {**self._config, "ai_tool": {**self._config["ai_tool"], "allow_auto_reply": True}}
        render = AsyncMock(return_value=self._audio)
        with (
            patch.object(tts_ai_tool, "get_config", Mock(return_value=cfg)),
            patch.object(tts_ai_tool, "_render_fish_tts", render),
        ):
            result = await tts_ai_tool.ai_send_voice(
                self._context(auto=True),
                {"text": "我自己插一句"},
            )

        self.assertTrue(result["ok"])
        self._bot.send.assert_awaited_once()

    async def test_disabled_tts_returns_error(self) -> None:
        cfg = {**self._config, "enabled": False}
        with patch.object(tts_ai_tool, "get_config", Mock(return_value=cfg)):
            result = await tts_ai_tool.ai_send_voice(
                self._context(),
                {"text": "你好"},
            )

        self.assertEqual(result["error"], "TTS is disabled")
        self._bot.send.assert_not_awaited()

    async def test_cooldown_blocks_rapid_second_call(self) -> None:
        render = AsyncMock(return_value=self._audio)
        with (
            patch.object(tts_ai_tool, "get_config", Mock(return_value=self._config)),
            patch.object(tts_ai_tool, "_render_fish_tts", render),
        ):
            first = await tts_ai_tool.ai_send_voice(self._context(), {"text": "第一句"})
            second = await tts_ai_tool.ai_send_voice(self._context(), {"text": "第二句"})

        self.assertTrue(first["ok"])
        self.assertIn("cooling down", second["error"])
        self.assertEqual(render.await_count, 1)
        self._bot.send.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
