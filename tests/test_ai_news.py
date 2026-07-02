from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import plugins.ai_news as ai_news_plugin
from plugins.ai_news import storage as ai_news_storage
from plugins.ai_news.feed import NewsItem, NewsSource, normalize_sources, parse_feed_xml, select_items
from plugins.push_framework.registry import PushContext, PushTarget, build_push_messages

SHANGHAI_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


class AiNewsFeedTests(unittest.TestCase):
    def test_parse_rss_item(self) -> None:
        source = NewsSource(
            id="example",
            title="Example AI",
            group="official",
            url="https://example.com/feed.xml",
            weight=10,
        )

        items = parse_feed_xml(
            """
            <rss version="2.0">
              <channel>
                <item>
                  <title>New AI Model</title>
                  <link>/news/model</link>
                  <guid>model-1</guid>
                  <pubDate>Tue, 30 Jun 2026 09:30:00 +0800</pubDate>
                  <description><![CDATA[<p>Hello <b>model</b></p>]]></description>
                </item>
              </channel>
            </rss>
            """,
            source=source,
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "New AI Model")
        self.assertEqual(items[0].link, "https://example.com/news/model")
        self.assertEqual(items[0].summary, "Hello model")
        self.assertEqual(items[0].published, datetime(2026, 6, 30, 9, 30, tzinfo=SHANGHAI_TZ))

    def test_normalize_sources_filters_by_group_and_id(self) -> None:
        cfg = {
            "sources": [
                {"id": "official_one", "enabled": True, "title": "Official", "group": "official", "url": "https://example.com/rss", "weight": 5},
                {"id": "media_one", "enabled": True, "title": "Media", "group": "media", "url": "https://example.com/media", "weight": 1},
            ]
        }

        sources = normalize_sources(cfg, {"groups": ["official"], "source_ids": []})

        self.assertEqual([source.id for source in sources], ["official_one"])

    def test_select_items_dedupes_and_prefers_weighted_source(self) -> None:
        now = datetime(2026, 6, 30, 12, tzinfo=timezone.utc)
        low = NewsItem("low", "Low", "media", "Same Title", "https://example.com/a?utm_source=x", published=now, weight=1)
        high = NewsItem("high", "High", "official", "Same Title", "https://example.com/a", published=now, weight=20)
        old = NewsItem("old", "Old", "media", "Old", "https://example.com/old", published=now - timedelta(days=20), weight=100)

        selected = select_items([low, high, old], max_items=5, max_age_hours=72, now=now, keyword_boosts=[])

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].source_id, "high")


class AiNewsStorageTests(unittest.TestCase):
    def test_seen_state_tracks_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "ai_news_state.json"
            with patch.object(ai_news_storage, "STATE_PATH", state_path):
                self.assertFalse(ai_news_storage.has_seen_state("daily"))
                self.assertEqual(ai_news_storage.unseen_keys("daily", ["a", "b"]), ["a", "b"])
                ai_news_storage.mark_seen("daily", ["a", "b", "c"], max_entries=2)
                self.assertTrue(ai_news_storage.has_seen_state("daily"))
                self.assertEqual(len(ai_news_storage.unseen_keys("daily", ["a", "b", "c"])), 1)


class AiNewsPushSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_push_source_builds_image_message(self) -> None:
        now = datetime(2026, 6, 30, 12, tzinfo=SHANGHAI_TZ)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {
                "enabled": True,
                "max_items": 3,
                "max_age_hours": 168,
                "only_new": True,
                "send_first_run": True,
                "max_state_entries": 100,
                "cache_dir": tmp,
                "render": {"image_format": "PNG"},
                "keyword_boosts": ["AI"],
                "sources": [
                    {
                        "id": "unit",
                        "enabled": True,
                        "title": "Unit Source",
                        "group": "official",
                        "url": "https://example.com/feed.xml",
                        "weight": 10,
                    }
                ],
            }
            item = NewsItem(
                source_id="unit",
                source_title="Unit Source",
                source_group="official",
                title="AI release",
                link="https://example.com/ai",
                summary="A useful release.",
                published=now,
                identity="ai-1",
                weight=10,
            )

            async def fake_fetch_all_sources(sources, config):
                return [item]

            ctx = PushContext(
                bot=None,
                job_id="ai_news_job",
                source="ai_news",
                target=PushTarget("group", 100),
                options={"mark_seen": False},
                now=now,
            )

            with (
                patch.object(ai_news_plugin, "get_config", Mock(return_value=cfg)),
                patch.object(ai_news_plugin, "fetch_all_sources", fake_fetch_all_sources),
            ):
                messages = await build_push_messages("ai_news", ctx)

            self.assertEqual(len(messages), 1)
            self.assertIn("image", str(messages[0].message))


if __name__ == "__main__":
    unittest.main()
