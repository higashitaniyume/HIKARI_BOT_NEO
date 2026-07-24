import os
import tempfile
import time
import unittest
from pathlib import Path

from core.storage import cleanup_files
from core.storage.cache_marker import (
    EXPIRY_FILE_NAME,
    cleanup_expired_marked_in,
    mark_files_expire_after,
    stamp_subdir,
)


class CacheCleanupTests(unittest.TestCase):
    def test_expired_marked_subdir_is_cleaned_after_persisted_deadline(self):
        with tempfile.TemporaryDirectory() as root:
            subdir = Path(root) / "media"
            stamp_subdir(str(subdir))
            video_path = subdir / "video_0.mp4"
            video_path.write_bytes(b"video")

            marked = mark_files_expire_after([str(video_path)], 10, now=100.0)

            self.assertEqual(marked, 1)
            self.assertTrue((subdir / EXPIRY_FILE_NAME).is_file())
            self.assertEqual(
                cleanup_expired_marked_in(root, ttl_seconds=10, now=109.0),
                (0, 0),
            )
            self.assertTrue(subdir.exists())

            cleaned_subdirs, cleaned_files = cleanup_expired_marked_in(
                root,
                ttl_seconds=10,
                now=111.0,
            )

            self.assertEqual(cleaned_subdirs, 1)
            self.assertEqual(cleaned_files, 3)
            self.assertFalse(subdir.exists())

    def test_expired_cleanup_ignores_unmarked_directories(self):
        with tempfile.TemporaryDirectory() as root:
            subdir = Path(root) / "foreign"
            subdir.mkdir()
            (subdir / "video_0.mp4").write_bytes(b"video")

            cleaned = cleanup_expired_marked_in(
                root,
                ttl_seconds=10,
                now=time.time() + 3600,
            )

            self.assertEqual(cleaned, (0, 0))
            self.assertTrue(subdir.exists())

    def test_cleanup_files_removes_parent_with_only_marker_metadata_left(self):
        with tempfile.TemporaryDirectory() as root:
            subdir = Path(root) / "media"
            stamp_subdir(str(subdir))
            image_path = subdir / "image_0.jpg"
            image_path.write_bytes(b"image")
            mark_files_expire_after([str(image_path)], 10, now=100.0)

            cleanup_files([str(image_path)])

            self.assertFalse(subdir.exists())

    def test_legacy_marked_subdir_without_deadline_uses_safe_ttl_grace(self):
        with tempfile.TemporaryDirectory() as root:
            subdir = Path(root) / "legacy"
            stamp_subdir(str(subdir))
            video_path = subdir / "video_0.mp4"
            video_path.write_bytes(b"video")

            old_time = time.time() - 7200
            for path in subdir.iterdir():
                os.utime(path, (old_time, old_time))
            os.utime(subdir, (old_time, old_time))

            cleaned_subdirs, _ = cleanup_expired_marked_in(
                root,
                ttl_seconds=300,
                now=time.time(),
            )

            self.assertEqual(cleaned_subdirs, 1)
            self.assertFalse(subdir.exists())


if __name__ == "__main__":
    unittest.main()
