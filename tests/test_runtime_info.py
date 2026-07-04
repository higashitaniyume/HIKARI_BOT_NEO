from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.runtime_info import format_duration, read_runtime_info


class RuntimeInfoTests(unittest.TestCase):
    def test_read_runtime_info_prefers_version_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "version.json").write_text(
                json.dumps(
                    {
                        "version": "1.2.3",
                        "git_commit": "abcdef1234567890",
                        "git_commit_short": "abcdef1",
                        "git_dirty": True,
                        "generated_at": "2026-07-04T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )

            info = read_runtime_info(root)

        self.assertEqual(info.version, "1.2.3")
        self.assertEqual(info.git_commit, "abcdef1234567890")
        self.assertEqual(info.git_commit_short, "abcdef1")
        self.assertTrue(info.git_dirty)
        self.assertEqual(info.generated_at, "2026-07-04T00:00:00Z")

    def test_format_duration_uses_compact_chinese_units(self) -> None:
        self.assertEqual(format_duration(42), "42秒")
        self.assertEqual(format_duration(65), "1分钟")
        self.assertEqual(format_duration(3661), "1小时1分钟")
        self.assertEqual(format_duration(90061), "1天1小时1分钟")


if __name__ == "__main__":
    unittest.main()
