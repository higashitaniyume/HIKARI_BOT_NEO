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
from plugins.sts2_wiki.config import _migrate_config
from plugins.sts2_wiki.models import Sts2WikiResult
from plugins.sts2_wiki.service import (
    Sts2WikiKeywordEmpty,
    Sts2WikiService,
    _cache_namespace,
    normalize_keyword,
    resolve_query_alias,
)


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


class FakeSpireCodexClient(Sts2WikiClient):
    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.requests: list[tuple[str, dict[str, Any]]] = []

    async def _request_spire(self, endpoint: str, params: dict[str, Any]) -> Any:
        self.requests.append((endpoint, dict(params)))
        keyword = params.get("search")
        if endpoint == "cards" and keyword == "打击":
            return [
                {
                    "id": "BEGONE",
                    "name": "下去！",
                    "description": "选择一张牌，将其变化为[gold]仆从打击[/gold]。",
                    "cost": 1,
                    "type": "技能",
                    "rarity": "普通",
                    "color": "regent",
                },
                {
                    "id": "STRIKE_IRONCLAD",
                    "name": "打击",
                    "description": "造成6点伤害。",
                    "cost": 1,
                    "type": "攻击",
                    "rarity": "初始牌",
                    "color": "ironclad",
                },
            ]
        if endpoint == "cards" and keyword == "完美打击":
            return [
                {
                    "id": "PERFECTED_STRIKE",
                    "name": "完美打击",
                    "description": "造成6点伤害。\n你每有一张名字中含有“打击”的牌，伤害+2。",
                    "upgrade_description": "造成9点伤害。\n你每有一张名字中含有“打击”的牌，伤害+3。",
                    "cost": 2,
                    "type": "攻击",
                    "rarity": "普通",
                    "color": "ironclad",
                }
            ]
        if endpoint == "cards" and keyword == "铁甲战士":
            return []
        if endpoint == "characters" and keyword == "铁甲战士":
            return [
                {
                    "id": "IRONCLAD",
                    "name": "铁甲战士",
                    "description": "铁甲军团最后的士兵。",
                    "starting_hp": 80,
                    "starting_gold": 99,
                    "max_energy": 3,
                }
            ]
        return []


class _FakeCtx:
    def __init__(self, args: str) -> None:
        self.args = args
        self.sent: list[str] = []
        self.event = None  # stats_increment 的 mock 在 patch 中覆盖

    async def send(self, message) -> None:
        self.sent.append(str(message))


def _cfg(**overrides: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "enabled": True,
        "source": "spire_codex",
        "api_url": "https://spire-codex.com/api",
        "site_url": "https://spire-codex.com",
        "language": "zhs",
        "version": "",
        "cache_ttl_seconds": 86400,
        "timeout": 10,
        "search_limit": 5,
        "summary_max_chars": 300,
        "query_max_chars": 80,
        "max_cache_entries": 500,
        "proxy": "",
        "user_agent": "HikariBot/1.0 SlayTheSpire2WikiQuery",
        "search_categories": ["cards", "characters", "relics", "potions", "powers", "keywords"],
        "query_aliases": {
            "Ironclad": "铁甲战士",
            "Strike": "打击",
            "Perfected Strike": "完美打击",
            "铁甲": "铁甲战士",
        },
    }
    cfg.update(overrides)
    return cfg


def _mediawiki_cfg(**overrides: Any) -> dict[str, Any]:
    cfg = _cfg(
        source="mediawiki",
        api_url="https://slaythespire.wiki.gg/api.php",
        query_aliases={
            "铁甲战士": "Ironclad",
            "打击": "Strike",
            "完美打击": "Perfected Strike",
        },
    )
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
        result = await FakeExtractClient(_mediawiki_cfg()).search("Strike")

        self.assertEqual(result.title, "Strike")
        self.assertEqual(result.summary, "Strike is a basic attack card.")
        self.assertIn("It deals damage", result.extract)
        self.assertEqual(result.url, "https://slaythespire.wiki.gg/wiki/Strike")
        self.assertEqual([item.title for item in result.candidates], ["Strike", "Strike+"])

    async def test_search_falls_back_to_parse_when_extract_is_empty(self) -> None:
        result = await FakeParseFallbackClient(_mediawiki_cfg()).search("Defend")

        self.assertEqual(result.title, "Defend")
        self.assertEqual(result.summary, "Defend is a basic Skill card.")
        self.assertNotIn("ignore", result.extract)
        self.assertEqual(result.url, "https://slaythespire.wiki.gg/wiki/Defend")

    async def test_spire_codex_prefers_exact_chinese_name_match(self) -> None:
        result = await FakeSpireCodexClient(_cfg(search_categories=["cards"])).search("打击")

        self.assertEqual(result.title, "打击（卡牌）")
        self.assertIn("卡牌 · 铁甲战士 · 攻击", result.summary)
        self.assertIn("造成6点伤害", result.extract)
        self.assertEqual(result.url, "https://spire-codex.com/zhs/cards/STRIKE_IRONCLAD")

    async def test_spire_codex_returns_chinese_card_detail(self) -> None:
        result = await FakeSpireCodexClient(_cfg(search_categories=["cards"])).search("完美打击")

        self.assertEqual(result.title, "完美打击（卡牌）")
        self.assertIn("你每有一张名字中含有“打击”的牌", result.extract)
        self.assertIn("升级：造成9点伤害", result.extract)

    async def test_spire_codex_searches_later_categories_for_characters(self) -> None:
        result = await FakeSpireCodexClient(_cfg(search_categories=["cards", "characters"])).search("铁甲战士")

        self.assertEqual(result.title, "铁甲战士（角色）")
        self.assertIn("生命 80", result.summary)
        self.assertIn("铁甲军团最后的士兵", result.extract)

    async def test_spire_codex_passes_configured_beta_version(self) -> None:
        client = FakeSpireCodexClient(_cfg(search_categories=["cards"], version="latest"))

        result = await client.search("打击")

        self.assertEqual(client.requests[0][1]["version"], "latest")
        self.assertEqual(result.url, "https://spire-codex.com/zhs/cards/STRIKE_IRONCLAD?version=latest")

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

    async def test_cache_namespace_separates_old_mediawiki_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sts2_cache.json"
            old_cache = Sts2WikiCache(path=path, ttl_seconds=86400, namespace="mediawiki|https://slaythespire.wiki.gg/api.php")
            new_cache = Sts2WikiCache(path=path, ttl_seconds=86400, namespace="spire_codex|zhs|https://spire-codex.com/api")
            await old_cache.set(
                "打击",
                Sts2WikiResult(
                    query="打击",
                    title="Strike (Ironclad)",
                    summary="English old cache.",
                    extract="English old cache.",
                    url="https://slaythespire.wiki.gg/wiki/Strike_(Ironclad)",
                ),
            )

            self.assertIsNone(await new_cache.get("打击"))
            old_result = await old_cache.get("打击")

        assert old_result is not None
        self.assertEqual(old_result.title, "Strike (Ironclad)")

    def test_cache_namespace_includes_spire_codex_version(self) -> None:
        stable = _cache_namespace(_cfg())
        beta = _cache_namespace(_cfg(version="latest"))

        self.assertNotEqual(stable, beta)
        self.assertIn("latest", beta)

    async def test_empty_keyword_normalization(self) -> None:
        with self.assertRaises(Sts2WikiKeywordEmpty):
            normalize_keyword("   ")

    def test_resolve_query_alias_matches_compact_chinese_terms(self) -> None:
        self.assertEqual(resolve_query_alias("Ironclad", _cfg()), "铁甲战士")
        self.assertEqual(resolve_query_alias("Perfected Strike", _cfg()), "完美打击")
        self.assertEqual(resolve_query_alias("铁甲", _cfg()), "铁甲战士")
        self.assertEqual(resolve_query_alias("unknown", _cfg()), "unknown")

    async def test_service_searches_alias_but_caches_original_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = _cfg()
            service = Sts2WikiService(cfg)
            service.cache = Sts2WikiCache(path=Path(tmpdir) / "cache.json", ttl_seconds=86400)
            service.client.search = AsyncMock(
                return_value=Sts2WikiResult(
                    query="打击",
                    title="打击（卡牌）",
                    summary="卡牌 · 铁甲战士 · 攻击 · 初始牌 · 费用 1",
                    extract="卡牌 · 铁甲战士 · 攻击 · 初始牌 · 费用 1\n造成6点伤害。",
                    url="https://spire-codex.com/zhs/cards/STRIKE_IRONCLAD",
                )
            )

            result = await service.lookup("Strike")
            cached = await service.cache.get("Strike")

        service.client.search.assert_awaited_once_with("打击")
        self.assertEqual(result.query, "Strike")
        assert cached is not None
        self.assertEqual(cached.query, "Strike")
        self.assertEqual(cached.title, "打击（卡牌）")

    def test_spire_codex_config_migrates_legacy_defaults(self) -> None:
        cfg = _migrate_config(
            _cfg(
                api_url="https://slaythespire.wiki.gg/api.php",
                query_aliases={
                    "打击": "Strike",
                    "完美打击": "Perfected Strike",
                    "custom": "自定义",
                },
            )
        )

        self.assertEqual(cfg["api_url"], "https://spire-codex.com/api")
        self.assertNotEqual(cfg["query_aliases"].get("打击"), "Strike")
        self.assertEqual(cfg["query_aliases"]["custom"], "自定义")

    async def test_command_failure_is_friendly_and_does_not_raise(self) -> None:
        ctx = _FakeCtx("Strike")
        with (
            patch.object(sts2_wiki_plugin, "get_config", Mock(return_value=_cfg())),
            patch.object(sts2_wiki_plugin, "msg", Mock(return_value="friendly failure")),
            patch.object(sts2_wiki_plugin, "stats_increment", Mock()),
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
