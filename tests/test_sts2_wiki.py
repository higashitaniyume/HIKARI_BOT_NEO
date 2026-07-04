from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import plugins.sts2_wiki as sts2_wiki_plugin
from plugins.aiagent.tools import registry as aiagent_tools
from plugins.sts2_wiki.api import Sts2WikiClient, Sts2WikiError
from plugins.sts2_wiki.cache import Sts2WikiCache
from plugins.sts2_wiki.models import Sts2WikiResult
from plugins.sts2_wiki.service import Sts2WikiKeywordEmpty, Sts2WikiService, normalize_keyword, resolve_query_alias


class FakeExtractClient(Sts2WikiClient):
    async def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        if params.get("list") == "search":
            return {
                "query": {
                    "search": [
                        {
                            "title": "Strike",
                            "snippet": "Deal <span class=\"searchmatch\">6</span> damage.",
                        },
                        {"title": "Strike+", "snippet": "Deal 9 damage."},
                    ]
                }
            }
        if params.get("prop") == "extracts|info":
            return {
                "query": {
                    "pages": [
                        {
                            "title": "Strike",
                            "extract": "Strike is a basic attack card.\n\nIt deals damage to an enemy.",
                            "fullurl": "https://slaythespire.wiki.gg/wiki/Strike",
                        }
                    ]
                }
            }
        raise AssertionError(f"unexpected params: {params}")


class FakeParseFallbackClient(Sts2WikiClient):
    async def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        if params.get("list") == "search":
            return {"query": {"search": [{"title": "Defend", "snippet": "Gain Block."}]}}
        if params.get("prop") == "extracts|info":
            return {
                "query": {
                    "pages": [
                        {
                            "title": "Defend",
                            "extract": "",
                            "fullurl": "https://slaythespire.wiki.gg/wiki/Defend",
                        }
                    ]
                }
            }
        if params.get("action") == "parse":
            return {
                "parse": {
                    "title": "Defend",
                    "text": "<table><tr><td>ignore</td></tr></table><p>Defend is a basic Skill card.</p>",
                    "wikitext": "'''Defend''' is a backup text.",
                }
            }
        raise AssertionError(f"unexpected params: {params}")


class _FakeCtx:
    def __init__(self, args: str) -> None:
        self.args = args
        self.sent: list[str] = []

    async def send(self, message) -> None:
        self.sent.append(str(message))


def _cfg(**overrides: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "enabled": True,
        "api_url": "https://slaythespire.wiki.gg/api.php",
        "cache_ttl_seconds": 86400,
        "timeout": 10,
        "search_limit": 5,
        "summary_max_chars": 300,
        "query_max_chars": 80,
        "max_cache_entries": 500,
        "proxy": "",
        "user_agent": "HikariBot/1.0 SlayTheSpire2WikiQuery",
        "query_aliases": {
            "铁甲战士": "Ironclad",
            "打击": "Strike",
            "完美打击": "Perfected Strike",
        },
    }
    cfg.update(overrides)
    return cfg


def _call(name: str, arguments: str = "{}") -> dict[str, object]:
    return {
        "id": f"call_{name}",
        "function": {
            "name": name,
            "arguments": arguments,
        },
    }


def _agent_cfg(plugin_tools: dict[str, object]) -> dict[str, object]:
    return {
        "tools": {
            "search": {"enabled": False},
            "files": {"enabled": False},
            "plugin_tools": plugin_tools,
            "max_tool_rounds": 2,
        }
    }


class Sts2WikiTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_uses_mediawiki_search_then_extracts(self) -> None:
        result = await FakeExtractClient(_cfg()).search("Strike")

        self.assertEqual(result.title, "Strike")
        self.assertEqual(result.summary, "Strike is a basic attack card.")
        self.assertIn("It deals damage", result.extract)
        self.assertEqual(result.url, "https://slaythespire.wiki.gg/wiki/Strike")
        self.assertEqual([item.title for item in result.candidates], ["Strike", "Strike+"])

    async def test_search_falls_back_to_parse_when_extract_is_empty(self) -> None:
        result = await FakeParseFallbackClient(_cfg()).search("Defend")

        self.assertEqual(result.title, "Defend")
        self.assertEqual(result.summary, "Defend is a basic Skill card.")
        self.assertNotIn("ignore", result.extract)
        self.assertEqual(result.url, "https://slaythespire.wiki.gg/wiki/Defend")

    async def test_cache_write_read_and_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sts2_cache.json"
            cache = Sts2WikiCache(path=path, ttl_seconds=86400, max_entries=10)
            await cache.set(
                "Strike",
                Sts2WikiResult(
                    query="Strike",
                    title="Strike",
                    summary="Strike is a card.",
                    extract="Strike is a card.",
                    url="https://slaythespire.wiki.gg/wiki/Strike",
                ),
            )

            cached = await cache.get(" strike ")
            assert cached is not None
            self.assertTrue(cached.cache_hit)
            self.assertEqual(cached.title, "Strike")

            data = json.loads(path.read_text(encoding="utf-8"))
            data["entries"]["strike"]["updated_at"] = (
                datetime.now(timezone.utc) - timedelta(days=2)
            ).isoformat(timespec="seconds")
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            self.assertIsNone(await cache.get("Strike"))

    async def test_empty_keyword_normalization(self) -> None:
        with self.assertRaises(Sts2WikiKeywordEmpty):
            normalize_keyword("   ")

    def test_resolve_query_alias_matches_compact_chinese_terms(self) -> None:
        self.assertEqual(resolve_query_alias(" 铁甲 战士 ", _cfg()), "Ironclad")
        self.assertEqual(resolve_query_alias("打击", _cfg()), "Strike")
        self.assertEqual(resolve_query_alias("unknown", _cfg()), "unknown")

    async def test_service_searches_alias_but_caches_original_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _cfg()
            service = Sts2WikiService(cfg)
            service.cache = Sts2WikiCache(path=Path(tmpdir) / "cache.json", ttl_seconds=86400)
            service.client.search = AsyncMock(
                return_value=Sts2WikiResult(
                    query="Strike",
                    title="Strike (Ironclad)",
                    summary="Strike is a basic attack card.",
                    extract="Strike is a basic attack card.",
                    url="https://slaythespire.wiki.gg/wiki/Strike_(Ironclad)",
                )
            )

            result = await service.lookup("打击")
            cached = await service.cache.get("打击")

        service.client.search.assert_awaited_once_with("Strike")
        self.assertEqual(result.query, "打击")
        assert cached is not None
        self.assertEqual(cached.query, "打击")
        self.assertEqual(cached.title, "Strike (Ironclad)")

    async def test_command_failure_is_friendly_and_does_not_raise(self) -> None:
        ctx = _FakeCtx("Strike")
        with (
            patch.object(sts2_wiki_plugin, "get_config", Mock(return_value=_cfg())),
            patch.object(sts2_wiki_plugin, "msg", Mock(return_value="friendly failure")),
            patch.object(
                sts2_wiki_plugin.Sts2WikiService,
                "lookup",
                AsyncMock(side_effect=Sts2WikiError("timeout")),
            ),
        ):
            await sts2_wiki_plugin.handle_sts2_wiki(ctx)

        self.assertEqual(ctx.sent, ["friendly failure"])

    async def test_aiagent_tool_executes_registered_sts2_wiki_search(self) -> None:
        with (
            patch.object(sts2_wiki_plugin, "get_config", Mock(return_value=_cfg())),
            patch.object(
                sts2_wiki_plugin.Sts2WikiService,
                "lookup",
                AsyncMock(
                    return_value=Sts2WikiResult(
                        query="Strike",
                        title="Strike",
                        summary="Strike is a card.",
                        extract="Strike is a card.",
                        url="https://slaythespire.wiki.gg/wiki/Strike",
                        cache_hit=True,
                    )
                ),
            ) as lookup_mock,
        ):
            result = await aiagent_tools.execute_tool_call(
                _agent_cfg({"enabled": True, "enabled_names": ["sts2_wiki_search"]}),
                _call("sts2_wiki_search", "{\"query\":\"Strike\"}"),
            )

        payload = json.loads(result["content"])
        self.assertEqual(payload["results"][0]["title"], "Strike")
        self.assertEqual(payload["results"][0]["url"], "https://slaythespire.wiki.gg/wiki/Strike")
        self.assertTrue(payload["cache_hit"])
        lookup_mock.assert_awaited_once_with("Strike")


if __name__ == "__main__":
    unittest.main()
