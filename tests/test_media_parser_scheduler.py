from __future__ import annotations

import unittest
from unittest.mock import AsyncMock
from types import SimpleNamespace

from core.config_loader import DEFAULT_MEDIA_PARSER_CONFIG
import plugins.media_parser as media_parser
from plugins.media_parser.bilibili_cookie_assist import BilibiliCookieAssistManager
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

    def test_bilibili_assist_trigger_consumes_parser_request(self) -> None:
        auth_runtime = object()

        class FakeParser:
            def __init__(self) -> None:
                self.consumed = False

            def consume_assist_request(self) -> str | None:
                if self.consumed:
                    return None
                self.consumed = True
                return "missing_cookie"

            def get_auth_runtime(self) -> object:
                return auth_runtime

        class FakeAssist:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def trigger_assist_request(self, bot, **kwargs) -> None:
                self.calls.append({"bot": bot, **kwargs})

        fake_parser = FakeParser()
        fake_assist = FakeAssist()
        old_assist = media_parser.bilibili_cookie_assist
        media_parser.bilibili_cookie_assist = fake_assist
        try:
            runtime = SimpleNamespace(
                config_manager=SimpleNamespace(
                    bilibili_parser=fake_parser,
                    bilibili=SimpleNamespace(
                        admin_reply_timeout_minutes=12,
                        admin_request_cooldown_minutes=34,
                    ),
                )
            )

            media_parser._trigger_bilibili_cookie_assist_if_needed(object(), runtime)
        finally:
            media_parser.bilibili_cookie_assist = old_assist

        self.assertEqual(len(fake_assist.calls), 1)
        self.assertEqual(fake_assist.calls[0]["reason"], "missing_cookie")
        self.assertIs(fake_assist.calls[0]["auth_runtime"], auth_runtime)
        self.assertEqual(fake_assist.calls[0]["reply_timeout_minutes"], 12)
        self.assertEqual(fake_assist.calls[0]["request_cooldown_minutes"], 34)

    def test_bilibili_assist_reply_is_superuser_private_only(self) -> None:
        class FakePrivateEvent:
            def __init__(self, user_id: str) -> None:
                self.user_id = user_id

            def get_user_id(self) -> str:
                return self.user_id

        self.assertTrue(
            BilibiliCookieAssistManager._is_superuser_private_event(
                FakePrivateEvent("12345"),
                "12345",
            )
        )
        self.assertFalse(
            BilibiliCookieAssistManager._is_superuser_private_event(
                FakePrivateEvent("54321"),
                "12345",
            )
        )


class MediaParserBilibiliLoginCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_cookie_login_command_starts_assist_flow(self) -> None:
        auth_runtime = object()
        bot = object()

        class FakeAssist:
            def __init__(self) -> None:
                self.started: list[dict[str, object]] = []

            def is_superuser_event(self, event) -> bool:
                return True

            async def start_manual_login(self, bot_arg, **kwargs) -> bool:
                self.started.append({"bot": bot_arg, **kwargs})
                return True

        class FakeParser:
            def get_auth_runtime(self) -> object:
                return auth_runtime

        fake_assist = FakeAssist()
        old_assist = media_parser.bilibili_cookie_assist
        old_runtime = media_parser._bilibili_cookie_login_runtime
        media_parser.bilibili_cookie_assist = fake_assist
        media_parser._bilibili_cookie_login_runtime = lambda: (
            FakeParser(),
            SimpleNamespace(admin_reply_timeout_minutes=9),
        )
        try:
            ctx = SimpleNamespace(
                bot=bot,
                event=object(),
                send=AsyncMock(),
            )

            await media_parser.bilibili_cookie_login_command(ctx)
        finally:
            media_parser.bilibili_cookie_assist = old_assist
            media_parser._bilibili_cookie_login_runtime = old_runtime

        self.assertEqual(len(fake_assist.started), 1)
        self.assertIs(fake_assist.started[0]["bot"], bot)
        self.assertIs(fake_assist.started[0]["auth_runtime"], auth_runtime)
        self.assertEqual(fake_assist.started[0]["reply_timeout_minutes"], 9)
        ctx.send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
