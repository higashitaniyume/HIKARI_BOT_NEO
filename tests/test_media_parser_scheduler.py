from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace

from core.config_loader import DEFAULT_MEDIA_PARSER_CONFIG
import plugins.media_parser as media_parser
from plugins.media_parser.bilibili_cookie_assist import BilibiliCookieAssistManager
from plugins.media_parser import sender


class MediaParserSchedulerTests(unittest.TestCase):
    def test_default_max_send_stays_high_for_multi_image_posts(self) -> None:
        self.assertEqual(DEFAULT_MEDIA_PARSER_CONFIG["max_send"], 80)
        self.assertEqual(DEFAULT_MEDIA_PARSER_CONFIG["parse_retry_count"], 2)
        self.assertEqual(DEFAULT_MEDIA_PARSER_CONFIG["parse_retry_delay_seconds"], 2.0)

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


class MediaParserRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_text_retries_full_parse_after_download_404(self) -> None:
        parser = SimpleNamespace(name="bilibili")
        parser_manager = _FakeParserManager(parser)
        download_manager = _FakeDownloadManager()
        runtime = SimpleNamespace(
            config={
                "enabled": True,
                "api_timeout": 30,
                "max_send": 80,
                "parse_retry_count": 1,
                "parse_retry_delay_seconds": 0,
                "download": {"cache_ttl_seconds": 600},
            },
            config_manager=SimpleNamespace(
                message=_FakeMessageConfig(),
                trigger=SimpleNamespace(should_parse=lambda text: True),
                proxy=SimpleNamespace(address=""),
                bilibili_parser=None,
            ),
            parser_manager=parser_manager,
            download_manager=download_manager,
        )

        event = SimpleNamespace(get_user_id=lambda: "10001")
        bot = SimpleNamespace(send=AsyncMock())

        with (
            patch.object(media_parser, "_runtime_from_config", Mock(return_value=runtime)),
            patch.object(media_parser, "_scope_allowed", Mock(return_value=True)),
            patch.object(media_parser, "_create_record_manager", Mock(return_value=SimpleNamespace(enabled=False))),
        ):
            result = await media_parser._prepare_text(
                bot,
                event,
                "https://b23.tv/example",
                force=True,
                links_with_parser=[("https://b23.tv/example", parser)],
            )

        self.assertIsNotNone(result)
        processed, _ = result
        self.assertEqual(parser_manager.parse_calls, 2)
        self.assertEqual(download_manager.download_calls, 2)
        self.assertTrue(processed[0]["has_valid_media"])
        self.assertEqual(processed[0]["video_modes"], ["local"])

    def test_retryable_download_result_ignores_size_limit_failures(self) -> None:
        result = media_parser.MediaPrepareAttempt(
            processed=[{
                "_enable_rich_media": True,
                "video_count": 1,
                "image_count": 0,
                "has_valid_media": False,
                "video_status_codes": [200],
                "image_status_codes": [],
                "video_skip_reasons": ["下载后视频大小超过限制（1200.0MB > 1000.0MB）"],
                "image_skip_reasons": [],
            }],
            metadata_list=[],
            config={},
        )

        self.assertFalse(media_parser._should_retry_prepare_result(result))


class _FakeMessageConfig:
    def output_for_metadata(self, metadata):
        return True, True


class _FakeParserManager:
    def __init__(self, parser) -> None:
        self.parser = parser
        self.parse_calls = 0

    def find_parser(self, url):
        return self.parser

    async def parse_text(self, text, session, links_with_parser=None):
        self.parse_calls += 1
        return [{
            "platform": "bilibili",
            "parser_name": "bilibili",
            "source_url": "https://b23.tv/example",
            "url": "https://www.bilibili.com/video/BV1example",
            "title": "retry example",
            "video_urls": [[f"https://upos.example/{self.parse_calls}.mp4"]],
            "image_urls": [],
            "video_force_downloads": [True],
        }]


class _FakeDownloadManager:
    def __init__(self) -> None:
        self.download_calls = 0

    async def process_metadata(self, *, session, metadata, proxy_addr=None):
        self.download_calls += 1
        metadata = dict(metadata)
        metadata["video_count"] = 1
        metadata["image_count"] = 0
        metadata["image_status_codes"] = []
        metadata["image_skip_reasons"] = []
        if self.download_calls == 1:
            metadata.update({
                "has_valid_media": False,
                "video_modes": ["skip"],
                "image_modes": [],
                "file_paths": [None],
                "video_status_codes": [404],
                "video_skip_reasons": ["缓存下载失败: HTTP 404: Not Found"],
            })
        else:
            metadata.update({
                "has_valid_media": True,
                "video_modes": ["local"],
                "image_modes": [],
                "file_paths": [__file__],
                "video_status_codes": [200],
                "video_skip_reasons": [None],
            })
        return metadata


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
