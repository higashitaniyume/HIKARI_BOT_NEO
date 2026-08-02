"""Netease parser processing tests — retry behavior on upload timeout."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from nonebot.adapters.onebot.v11 import NetworkError

from plugins.netease_parser.processing import _is_upload_timeout, _process_queue_item


def _make_item(item_type: str = "song", item_id: str = "1"):
    return SimpleNamespace(
        bot=AsyncMock(),
        event=SimpleNamespace(),
        item_type=item_type,
        item_id=item_id,
    )


class TestIsUploadTimeout(unittest.TestCase):
    def test_private_upload_timeout(self):
        exc = NetworkError("WebSocket call api upload_private_file timeout")
        self.assertTrue(_is_upload_timeout(exc))

    def test_group_upload_timeout(self):
        exc = NetworkError("WebSocket call api upload_group_file timeout")
        self.assertTrue(_is_upload_timeout(exc))

    def test_other_api_timeout_not_matched(self):
        exc = NetworkError("WebSocket call api send_msg timeout")
        self.assertFalse(_is_upload_timeout(exc))

    def test_upload_error_without_timeout_not_matched(self):
        exc = NetworkError("WebSocket call api upload_private_file rejected")
        self.assertFalse(_is_upload_timeout(exc))

    def test_non_network_error_not_matched(self):
        self.assertFalse(_is_upload_timeout(RuntimeError("upload timeout")))


class TestQueueItemRetry(unittest.TestCase):
    def test_upload_timeout_no_retry(self):
        """上传超时视为已送达，不重试，只通知一次。"""
        item = _make_item()
        cfg = {"parse_retry_count": 2, "parse_retry_delay_seconds": 0}

        with patch(
            "plugins.netease_parser.processing._process_single_song",
            new=AsyncMock(side_effect=NetworkError(
                "WebSocket call api upload_private_file timeout",
            )),
        ) as process, patch(
            "plugins.netease_parser.processing.notify_error_to_superuser",
            new=AsyncMock(),
        ) as notify:
            asyncio.run(_process_queue_item(item, cfg))

            self.assertEqual(process.call_count, 1, "上传超时不应触发重试")
            notify.assert_awaited_once()

    def test_generic_error_retries_then_fails(self):
        """普通错误按 parse_retry_count 重试，耗尽后通知。"""
        item = _make_item()
        cfg = {"parse_retry_count": 2, "parse_retry_delay_seconds": 0}

        with patch(
            "plugins.netease_parser.processing._process_single_song",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ) as process, patch(
            "plugins.netease_parser.processing.notify_error_to_superuser",
            new=AsyncMock(),
        ) as notify:
            asyncio.run(_process_queue_item(item, cfg))

            self.assertEqual(process.call_count, 3, "应重试 2 次，共 3 次处理")
            notify.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
