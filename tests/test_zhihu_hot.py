from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image

import plugins.zhihu_hot as zhihu_hot_plugin
from plugins.push_framework.registry import PushContext, PushTarget, build_push_messages
from plugins.zhihu_hot.api import ZhihuHotClient, ZhihuHotItem, parse_hot_list
from plugins.zhihu_hot.render import render_hot_list

SHANGHAI_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


class ZhihuHotApiTests(unittest.TestCase):
    def test_parse_hot_list_extracts_web_question_url(self) -> None:
        payload = {
            "data": [
                {
                    "detail_text": "582 万热度",
                    "debut": True,
                    "target": {
                        "id": 2055620022316131446,
                        "title": "机器人伴侣价格 11.98 万至 99 万，不同价格之间有啥区别？",
                        "url": "https://api.zhihu.com/questions/2055620022316131446",
                        "excerpt": "这是问题摘要。",
                        "answer_count": 267,
                        "follower_count": 375,
                    },
                }
            ]
        }

        items = parse_hot_list(payload, max_items=1)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].rank, 1)
        self.assertEqual(items[0].heat, "582 万热度")
        self.assertEqual(items[0].url, "https://www.zhihu.com/question/2055620022316131446")
        self.assertTrue(items[0].debut)


class ZhihuHotRenderTests(unittest.IsolatedAsyncioTestCase):
    async def test_render_hot_list_creates_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            item = ZhihuHotItem(
                rank=1,
                title="AI 公司发布新产品，市场怎么看？",
                url="https://www.zhihu.com/question/1",
                heat="100 万热度",
                excerpt="这是用于测试渲染换行的知乎热搜摘要。",
                answer_count=42,
                follower_count=100,
                question_id=1,
            )

            path = await render_hot_list(
                [item],
                config={"cache_dir": tmp, "render": {"image_format": "PNG"}},
                generated_at=datetime(2026, 7, 2, 12, tzinfo=SHANGHAI_TZ),
            )

            self.assertTrue(Path(path).exists())
            with Image.open(path) as image:
                self.assertEqual(image.size[0], 1180)
                self.assertGreater(image.size[1], 300)


class ZhihuHotPushSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_push_source_builds_image_message(self) -> None:
        now = datetime(2026, 7, 2, 12, tzinfo=SHANGHAI_TZ)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {
                "enabled": True,
                "api_url": "https://api.zhihu.com/topstory/hot-list",
                "timeout_seconds": 20,
                "cache_dir": tmp,
                "max_items": 5,
                "render": {"image_format": "PNG"},
            }
            item = ZhihuHotItem(
                rank=1,
                title="测试热搜问题",
                url="https://www.zhihu.com/question/100",
                heat="88 万热度",
                excerpt="测试摘要。",
                question_id=100,
            )
            ctx = PushContext(
                bot=None,
                job_id="zhihu_hot_job",
                source="zhihu_hot",
                target=PushTarget("group", 100),
                options={"max_items": 5},
                now=now,
            )

            with (
                patch.object(zhihu_hot_plugin, "get_config", Mock(return_value=cfg)),
                patch.object(ZhihuHotClient, "fetch_hot_items", AsyncMock(return_value=[item])) as fetch_mock,
            ):
                messages = await build_push_messages("zhihu_hot", ctx)

            self.assertEqual(len(messages), 1)
            self.assertIn("image", str(messages[0].message))
            fetch_mock.assert_awaited_once()

    async def test_push_source_can_include_links(self) -> None:
        now = datetime(2026, 7, 2, 12, tzinfo=SHANGHAI_TZ)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {
                "enabled": True,
                "api_url": "https://api.zhihu.com/topstory/hot-list",
                "timeout_seconds": 20,
                "cache_dir": tmp,
                "max_items": 5,
                "render": {"image_format": "PNG"},
            }
            item = ZhihuHotItem(
                rank=1,
                title="测试热搜问题",
                url="https://www.zhihu.com/question/100",
                heat="88 万热度",
                question_id=100,
            )
            ctx = PushContext(
                bot=None,
                job_id="zhihu_hot_job",
                source="zhihu_hot",
                target=PushTarget("group", 100),
                options={"max_items": 5, "include_links": True},
                now=now,
            )

            with (
                patch.object(zhihu_hot_plugin, "get_config", Mock(return_value=cfg)),
                patch.object(ZhihuHotClient, "fetch_hot_items", AsyncMock(return_value=[item])),
            ):
                messages = await build_push_messages("zhihu_hot", ctx)

            self.assertEqual(len(messages), 2)
            self.assertIn("https://www.zhihu.com/question/100", str(messages[1].message))


if __name__ == "__main__":
    unittest.main()
