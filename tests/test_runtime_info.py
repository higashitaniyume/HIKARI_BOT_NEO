from __future__ import annotations

import json
import subprocess
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
                        "versions": [
                            {
                                "version": "0.0.1",
                                "git_hash": "abcdef1",
                                "title": "Initial version",
                            },
                            {
                                "version": "0.0.2",
                                "git_hash": "abcdef2",
                                "title": "Current version",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            info = read_runtime_info(root)

        self.assertEqual(info.version, "0.0.2")
        self.assertEqual(info.git_hash, "abcdef2")
        self.assertEqual(info.title, "Current version")
        self.assertEqual(len(info.versions), 2)

    def test_read_runtime_info_accepts_legacy_version_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "version.json").write_text(
                json.dumps(
                    {
                        "version": "1.2.3",
                        "git_commit_short": "abcdef1",
                        "title": "Legacy version",
                    }
                ),
                encoding="utf-8",
            )

            info = read_runtime_info(root)

        self.assertEqual(info.version, "1.2.3")
        self.assertEqual(info.git_hash, "abcdef1")
        self.assertEqual(info.title, "Legacy version")
        self.assertEqual(len(info.versions), 1)

    def test_read_runtime_info_builds_versions_from_git_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)
            (root / "a.txt").write_text("a", encoding="utf-8")
            subprocess.run(["git", "add", "a.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "First title"], cwd=root, check=True, capture_output=True)
            (root / "a.txt").write_text("b", encoding="utf-8")
            subprocess.run(["git", "commit", "-am", "Second title"], cwd=root, check=True, capture_output=True)

            info = read_runtime_info(root)

        self.assertEqual([entry.version for entry in info.versions], ["0.0.1", "0.0.2"])
        self.assertEqual(info.title, "Second title")
        self.assertEqual(len(info.git_hash), 7)

    def test_format_duration_uses_compact_chinese_units(self) -> None:
        self.assertEqual(format_duration(42), "42秒")
        self.assertEqual(format_duration(65), "1分钟")
        self.assertEqual(format_duration(3661), "1小时1分钟")
        self.assertEqual(format_duration(90061), "1天1小时1分钟")


if __name__ == "__main__":
    unittest.main()
