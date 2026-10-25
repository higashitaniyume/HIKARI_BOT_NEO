from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import nonebot

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="nonebot.drivers.none:Driver")

from plugins.bot_admin import settings as admin_settings


CURRENT = {
    "enabled": True,
    "selected_voice": "永雏塔菲",
    "voices": [{"name": "永雏塔菲", "reference_id": "aaaa"}],
    "fish_audio": {"api_key": "secret"},
    "proxy": "",
    "connect_timeout": 10,
    "receive_timeout": 60,
    "max_chars": 120,
    "cooldown_seconds": 5,
    "cache_dir": "/tmp/hikari_bot/tts",
    "cache_ttl_minutes": 60,
    "ai_tool": {
        "max_chars": 80,
        "cooldown_seconds": 10,
        "allow_auto_reply": False,
    },
}


class BotAdminTTSConfigTests(unittest.TestCase):
    def _save(self, current: dict, payload: dict) -> dict:
        with (
            patch.object(admin_settings, "get_tts_config", Mock(return_value=current)),
            patch.object(admin_settings, "save_tts_config", Mock(side_effect=lambda data: data)),
        ):
            return admin_settings._update_tts_config(payload)

    def test_saves_ai_tool_settings(self) -> None:
        saved = self._save(
            CURRENT,
            {
                "ai_tool": {
                    "max_chars": 40,
                    "cooldown_seconds": 0,
                    "allow_auto_reply": True,
                }
            },
        )

        self.assertEqual(
            saved["ai_tool"],
            {"max_chars": 40, "cooldown_seconds": 0, "allow_auto_reply": True},
        )

    def test_keeps_ai_tool_settings_when_payload_omits_them(self) -> None:
        saved = self._save(CURRENT, {"max_chars": 100})

        self.assertEqual(saved["ai_tool"], CURRENT["ai_tool"])
        self.assertEqual(saved["max_chars"], 100)

    def test_clamps_ai_tool_limits(self) -> None:
        saved = self._save(
            CURRENT,
            {
                "ai_tool": {
                    "max_chars": 10**6,
                    "cooldown_seconds": -5,
                }
            },
        )

        self.assertEqual(saved["ai_tool"]["max_chars"], 1000)
        self.assertEqual(saved["ai_tool"]["cooldown_seconds"], 0)


if __name__ == "__main__":
    unittest.main()
