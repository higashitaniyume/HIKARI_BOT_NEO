from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import plugins.rss_subscriber as rss_plugin
from plugins.push_framework.registry import PushContext, PushTarget, build_push_messages
from plugins.rss_subscriber import storage as rss_storage
from plugins.rss_subscriber.feed import RssEntry, RssFeed, format_feed_message, parse_feed_xml


class RssFeedParsingTests(unittest.TestCase):
    def test_parse_rss_feed(self) -> None:
        feed = parse_feed_xml(
            """
            <rss version="2.0">
              <channel>
                <title>Example News</title>
                <item>
                  <title>First &amp; Fresh</title>
                  <link>/news/1</link>
                  <guid>entry-1</guid>
                  <pubDate>Tue, 30 Jun 2026 09:30:00 +0800</pubDate>
                  <description><![CDATA[<p>Hello <b>RSS</b></p>]]></description>
                </item>
              </channel>
            </rss>
            """,
            url="https://example.com/feed.xml",
        )

        self.assertEqual(feed.title, "Example News")
        self.assertEqual(len(feed.entries), 1)
        self.assertEqual(feed.entries[0].title, "First & Fresh")
        self.assertEqual(feed.entries[0].link, "https://example.com/news/1")
        self.assertEqual(feed.entries[0].summary, "Hello RSS")
        self.assertEqual(feed.entries[0].published, "2026-06-30T09:30+08:00")

    def test_parse_atom_feed(self) -> None:
        feed = parse_feed_xml(
            """
            <feed xmlns="http://www.w3.org/2005/Atom">
              <title>Atom Blog</title>
              <entry>
                <title>Atom Entry</title>
                <id>tag:example.com,2026:entry</id>
                <link rel="alternate" href="https://example.com/atom-entry"/>
                <updated>2026-06-30T01:30:00Z</updated>
                <summary>Atom summary</summary>
              </entry>
            </feed>
            """,
            url="https://example.com/atom.xml",
        )

        self.assertEqual(feed.title, "Atom Blog")
        self.assertEqual(feed.entries[0].link, "https://example.com/atom-entry")
        self.assertEqual(feed.entries[0].published, "2026-06-30T01:30+00:00")

    def test_format_message_truncates_to_limit(self) -> None:
        feed = parse_feed_xml(
            """
            <rss><channel><title>Long Feed</title></channel></rss>
            """,
            url="https://example.com/feed.xml",
        )
        entry = RssEntry(title="Long", link="https://example.com/long", summary="x" * 200, identity="long")

        message = format_feed_message(
            feed,
            [entry],
            include_summary=True,
            summary_max_chars=40,
            max_message_chars=90,
        )

        self.assertLessEqual(len(message), 90)
        self.assertIn("Long Feed 更新", message)


class RssStorageTests(unittest.TestCase):
    def test_seen_state_tracks_and_prunes_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "rss_state.json"
            with patch.object(rss_storage, "STATE_PATH", state_path):
                self.assertFalse(rss_storage.has_seen_state("news"))
                self.assertEqual(rss_storage.unseen_keys("news", ["a", "b"]), ["a", "b"])

                rss_storage.mark_seen("news", ["a", "b", "c"], max_entries=2)
                state = json.loads(state_path.read_text(encoding="utf-8"))
                remaining = set(state["seen"]["news"])

                self.assertTrue(rss_storage.has_seen_state("news"))
                self.assertEqual(len(remaining), 2)
                self.assertEqual(
                    rss_storage.unseen_keys("news", ["a", "b", "c", "d"]),
                    [key for key in ["a", "b", "c", "d"] if key not in remaining],
                )


class RssPushSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_push_source_builds_feed_message(self) -> None:
        cfg = {
            "enabled": True,
            "summary_max_chars": 220,
            "max_message_chars": 3500,
            "subscriptions": [
                {
                    "id": "news",
                    "enabled": True,
                    "title": "News",
                    "url": "https://example.com/feed.xml",
                    "max_items": 2,
                    "include_summary": True,
                    "summary_max_chars": 220,
                    "only_new": False,
                    "send_first_run": True,
                }
            ],
        }
        feed = RssFeed(
            title="News Feed",
            url="https://example.com/feed.xml",
            entries=[
                RssEntry(
                    title="Entry One",
                    link="https://example.com/1",
                    summary="Summary",
                    identity="entry-1",
                )
            ],
        )

        async def fake_fetch_feed(url: str, config: dict):
            return feed

        ctx = PushContext(
            bot=None,
            job_id="rss_job",
            source="rss_feed",
            target=PushTarget("group", 100),
            options={"subscription_id": "news", "only_new": False, "mark_seen": False},
            now=datetime(2026, 6, 30, tzinfo=timezone.utc),
        )

        with (
            patch.object(rss_plugin, "get_config", Mock(return_value=cfg)),
            patch.object(rss_plugin, "fetch_feed", fake_fetch_feed),
        ):
            messages = await build_push_messages("rss_feed", ctx)

        self.assertEqual(len(messages), 1)
        self.assertIn("Entry One", str(messages[0].message))
        self.assertIn("https://example.com/1", str(messages[0].message))


if __name__ == "__main__":
    unittest.main()
