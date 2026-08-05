"""Netease parser prefs tests — user quality preference + recent send records."""

import tempfile
import unittest
from pathlib import Path

from plugins.netease_parser import prefs


class TestQualityPrefs(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_path = prefs.PREFS_PATH
        prefs.PREFS_PATH = Path(self._tmp.name) / "netease_prefs.json"

    def tearDown(self):
        prefs.PREFS_PATH = self._orig_path
        prefs._recent.clear()
        self._tmp.cleanup()

    def test_default_quality_is_flac(self):
        self.assertEqual(prefs.get_user_quality("123"), "flac")

    def test_set_and_get_mp3(self):
        prefs.set_user_quality("123", "mp3")
        self.assertEqual(prefs.get_user_quality("123"), "mp3")

    def test_set_flac_overrides_mp3(self):
        prefs.set_user_quality("123", "mp3")
        prefs.set_user_quality("123", "flac")
        self.assertEqual(prefs.get_user_quality("123"), "flac")

    def test_invalid_quality_ignored(self):
        prefs.set_user_quality("123", "ogg")
        self.assertEqual(prefs.get_user_quality("123"), "flac")

    def test_pref_persists_across_loads(self):
        prefs.set_user_quality("123", "mp3")
        # 重新读文件（新实例路径相同），应保持偏好
        self.assertEqual(prefs.get_user_quality("123"), "mp3")

    def test_record_and_find_by_message_id(self):
        prefs.record_send(
            "123",
            item_type="song", item_id="42", title="曲", quality="flac",
            message_ids=["111", "222"],
        )
        rec = prefs.find_recent_by_message_id("123", "222")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.item_id, "42")
        self.assertEqual(rec.quality, "flac")
        self.assertEqual(rec.item_type, "song")

    def test_find_recent_limits_per_user(self):
        for i in range(10):
            prefs.record_send(
                "123",
                item_type="song", item_id=str(i), title="t", quality="flac",
                message_ids=[f"m{i}"],
            )
        self.assertIsNone(prefs.find_recent_by_message_id("123", "m0"))
        self.assertIsNotNone(prefs.find_recent_by_message_id("123", "m9"))

    def test_find_recent_scoped_to_user(self):
        prefs.record_send(
            "userA", item_type="song", item_id="1", title="a",
            quality="flac", message_ids=["m1"],
        )
        prefs.record_send(
            "userB", item_type="song", item_id="2", title="b",
            quality="flac", message_ids=["m2"],
        )
        self.assertIsNone(prefs.find_recent_by_message_id("userA", "m2"))
        self.assertIsNotNone(prefs.find_recent_by_message_id("userB", "m2"))

    def test_record_without_message_ids_not_found(self):
        prefs.record_send(
            "123",
            item_type="song", item_id="42", title="t", quality="flac",
            message_ids=[],
        )
        self.assertIsNone(prefs.find_recent_by_message_id("123", "m1"))


if __name__ == "__main__":
    unittest.main()
