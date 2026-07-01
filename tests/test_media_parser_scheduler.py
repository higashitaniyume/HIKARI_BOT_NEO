from __future__ import annotations

import unittest

from core.config_loader import DEFAULT_MEDIA_PARSER_CONFIG
import plugins.media_parser as media_parser
from plugins.media_parser import sender


class MediaParserSchedulerTests(unittest.TestCase):
    def test_default_max_send_stays_high_for_multi_image_posts(self) -> None:
        self.assertEqual(DEFAULT_MEDIA_PARSER_CONFIG["max_send"], 80)

    def test_queue_settings_reads_parse_concurrency(self) -> None:
        settings = media_parser._queue_settings({
            "parse_queue": {
                "enabled": True,
                "max_size": 10,
                "max_concurrent": 3,
                "delay_seconds": 0.25,
            }
        })

        self.assertEqual(settings["max_concurrent"], 3)
        self.assertEqual(settings["delay_seconds"], 0.25)

    def test_limit_metadata_for_send_keeps_counts_and_slices_media(self) -> None:
        metadata = {
            "platform": "douyin",
            "video_urls": [["v1"], ["v2"]],
            "image_urls": [["i1"], ["i2"], ["i3"], ["i4"], ["i5"]],
            "video_cover_urls": [["c1"], ["c2"]],
            "video_force_downloads": [True, False],
        }

        limited = media_parser._limit_metadata_for_send(metadata, max_send=4)

        self.assertEqual(limited["video_urls"], [["v1"], ["v2"]])
        self.assertEqual(limited["image_urls"], [["i1"], ["i2"]])
        self.assertEqual(limited["video_cover_urls"], [["c1"], ["c2"]])
        self.assertEqual(limited["video_force_downloads"], [True, False])
        self.assertEqual(limited["_original_video_count"], 2)
        self.assertEqual(limited["_original_image_count"], 5)
        self.assertEqual(len(metadata["image_urls"]), 5)

    def test_forward_chunks_can_follow_max_send(self) -> None:
        media_messages = [("image", object()) for _ in range(29)]

        chunks = sender._chunk_media_messages(media_messages, 80)

        self.assertEqual([len(chunk) for chunk in chunks], [29])


if __name__ == "__main__":
    unittest.main()
