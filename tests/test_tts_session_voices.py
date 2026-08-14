"""TTS per-session voice tests — session storage + effective voice resolution."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import plugins.tts_speaker as tts
from plugins.tts_speaker import session_voices

CONFIG = {
    "voices": [
        {"name": "永雏塔菲", "reference_id": "a"},
        {"name": "电棍", "reference_id": "b"},
    ],
    "selected_voice": "永雏塔菲",
}


class TestSessionVoicesStorage(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_path = session_voices.SESSION_VOICES_PATH
        session_voices.SESSION_VOICES_PATH = Path(self._tmp.name) / "tts_session_voices.json"

    def tearDown(self):
        session_voices.SESSION_VOICES_PATH = self._orig_path
        self._tmp.cleanup()

    def test_unset_session_returns_none(self):
        self.assertIsNone(session_voices.get_session_voice("group:1"))

    def test_set_and_get(self):
        session_voices.set_session_voice("group:1", "电棍")
        self.assertEqual(session_voices.get_session_voice("group:1"), "电棍")

    def test_override(self):
        session_voices.set_session_voice("user:1", "A")
        session_voices.set_session_voice("user:1", "B")
        self.assertEqual(session_voices.get_session_voice("user:1"), "B")

    def test_sessions_independent(self):
        session_voices.set_session_voice("group:1", "A")
        session_voices.set_session_voice("group:2", "B")
        self.assertIsNone(session_voices.get_session_voice("user:1"))
        self.assertEqual(session_voices.get_session_voice("group:1"), "A")
        self.assertEqual(session_voices.get_session_voice("group:2"), "B")

    def test_persists_across_loads(self):
        session_voices.set_session_voice("group:1", "A")
        # 重新读文件（同一路径），应保持配置
        self.assertEqual(session_voices.get_session_voice("group:1"), "A")

    def test_empty_name_ignored(self):
        session_voices.set_session_voice("group:1", "  ")
        self.assertIsNone(session_voices.get_session_voice("group:1"))

    def test_session_key_group(self):
        event = SimpleNamespace(group_id="123", get_user_id=lambda: "456")
        self.assertEqual(session_voices.session_key(event), "group:123")

    def test_session_key_private(self):
        event = SimpleNamespace(get_user_id=lambda: "456")
        self.assertEqual(session_voices.session_key(event), "user:456")


class TestEffectiveVoice(unittest.TestCase):
    """会话音色解析：会话音色优先，未设置/已失效时回退全局默认。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_path = session_voices.SESSION_VOICES_PATH
        session_voices.SESSION_VOICES_PATH = Path(self._tmp.name) / "tts_session_voices.json"

    def tearDown(self):
        session_voices.SESSION_VOICES_PATH = self._orig_path
        self._tmp.cleanup()

    def test_default_when_no_session_voice(self):
        self.assertEqual(tts._effective_voice(CONFIG, "group:1"), "永雏塔菲")

    def test_session_voice_used_when_valid(self):
        session_voices.set_session_voice("group:1", "电棍")
        self.assertEqual(tts._effective_voice(CONFIG, "group:1"), "电棍")

    def test_falls_back_to_default_when_session_voice_invalid(self):
        session_voices.set_session_voice("group:1", "不存在的音色")
        self.assertEqual(tts._effective_voice(CONFIG, "group:1"), "永雏塔菲")

    def test_falls_back_to_default_when_global_default_invalid(self):
        cfg = {
            "voices": [{"name": "电棍", "reference_id": "b"}],
            "selected_voice": "已删除的音色",
        }
        self.assertEqual(tts._effective_voice(cfg, "group:1"), "已删除的音色")
        # 渲染时若最终音色在列表中不存在则报错（保持原有校验行为）
        with self.assertRaises(RuntimeError):
            tts._selected_voice(cfg, tts._effective_voice(cfg, "group:1"))

    def test_selected_voice_validates_by_name(self):
        self.assertEqual(
            tts._selected_voice(CONFIG, "电棍"),
            {"name": "电棍", "reference_id": "b"},
        )


if __name__ == "__main__":
    unittest.main()
