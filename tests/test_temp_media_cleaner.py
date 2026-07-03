from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from core.temp_media_cleaner import (
    cleanup_expired_temp_media,
    register_temp_media_path,
)


class TempMediaCleanerTests(unittest.TestCase):
    def test_cleanup_deletes_expired_registered_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = root / "registry.json"
            media_file = root / "image.jpg"
            media_file.write_bytes(b"image")

            register_temp_media_path(media_file, ttl_seconds=1, registry_path=registry)
            result = cleanup_expired_temp_media(now=time.time() + 2, registry_path=registry)

            self.assertFalse(media_file.exists())
            self.assertEqual(result.deleted, 1)
            self.assertEqual(result.errors, 0)

    def test_cleanup_deletes_expired_marked_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = root / "registry.json"
            media_dir = root / "media_parser" / "bilibili_abc"
            media_dir.mkdir(parents=True)
            (media_dir / ".astrbot_media_parser").write_text("", encoding="utf-8")
            (media_dir / "video_0.mp4").write_bytes(b"video")

            register_temp_media_path(media_dir, ttl_seconds=1, kind="dir", registry_path=registry)
            result = cleanup_expired_temp_media(now=time.time() + 2, registry_path=registry)

            self.assertFalse(media_dir.exists())
            self.assertEqual(result.deleted, 1)
            self.assertEqual(result.errors, 0)

    def test_cleanup_refuses_unmarked_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = root / "registry.json"
            media_dir = root / "media_parser"
            media_dir.mkdir()
            (media_dir / "cookie.json").write_text("{}", encoding="utf-8")

            register_temp_media_path(media_dir, ttl_seconds=1, kind="dir", registry_path=registry)
            result = cleanup_expired_temp_media(now=time.time() + 2, registry_path=registry)

            self.assertTrue(media_dir.exists())
            self.assertEqual(result.deleted, 0)
            self.assertEqual(result.errors, 1)


if __name__ == "__main__":
    unittest.main()
