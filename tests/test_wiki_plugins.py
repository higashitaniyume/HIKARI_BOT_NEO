from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

import plugins.mc_wiki as mc_wiki_plugin
import plugins.stardew_wiki as stardew_wiki_plugin
from core import bot_messages
from plugins.mc_wiki.api import McWikiClient, McWikiResult
from plugins.stardew_wiki.api import StardewWikiClient


def _default_message(key: str, **kwargs) -> str:
    current: Any = bot_messages.DEFAULT_MESSAGES
    for part in key.split("."):
        current = current[part]
    text = str(current)
    return text.format(**kwargs) if kwargs else text


class FakeMcWikiClient(McWikiClient):
    async def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        if params.get("generator") == "search":
            return {
                "query": {
                    "pages": [
                        {
                            "index": 1,
                            "title": "苦力怕",
                            "fullurl": "https://zh.minecraft.wiki/w/苦力怕",
                        }
                    ]
                }
            }
        if params.get("prop") == "extracts":
            return {
                "query": {
                    "pages": [
                        {
                            "extract": "苦力怕是一种常见的敌对生物。它会悄悄接近玩家并爆炸。"
                        }
                    ]
                }
            }
        if params.get("prop") == "pageimages":
            return {
                "query": {
                    "pages": [
                        {
                            "original": {
                                "source": "https://zh.minecraft.wiki/images/Creeper.png"
                            }
                        }
                    ]
                }
            }
        raise AssertionError(f"unexpected params: {params}")


class FakeStardewWikiClient(StardewWikiClient):
    async def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        if params.get("generator") == "search":
            return {
                "query": {
                    "pages": [
                        {
                            "index": 1,
                            "title": "鱼",
                            "fullurl": "https://zh.stardewvalleywiki.com/鱼",
                        }
                    ]
                }
            }
        if params.get("action") == "parse":
            return {
                "parse": {
                    "text": (
                        "<table><tr><td>忽略信息框</td></tr></table>"
                        "<p>鱼可以通过钓鱼技能获得。</p>"
                        "<p>不同鱼类会在不同季节、天气和地点出现。</p>"
                    )
                }
            }
        if params.get("prop") == "pageimages":
            return {
                "query": {
                    "pages": [
                        {
                            "thumbnail": {
                                "source": "https://stardewvalleywiki.com/mediawiki/images/Fish.gif"
                            }
                        }
                    ]
                }
            }
        raise AssertionError(f"unexpected params: {params}")


class WikiPluginTests(unittest.IsolatedAsyncioTestCase):
    async def test_mc_wiki_result_includes_detail_and_main_image(self) -> None:
        result = await FakeMcWikiClient(
            {
                "api_url": "https://example.invalid/api.php",
                "summary_max_chars": 220,
                "detail_max_chars": 1600,
                "image_size": 640,
            }
        ).search("苦力怕")

        self.assertEqual(result.title, "苦力怕")
        self.assertIn("敌对生物", result.detail)
        self.assertEqual(result.summary, result.detail)
        self.assertEqual(result.image_url, "https://zh.minecraft.wiki/images/Creeper.png")

    async def test_stardew_wiki_result_uses_full_intro_paragraphs_and_main_image(self) -> None:
        result = await FakeStardewWikiClient(
            {
                "api_url": "https://example.invalid/api.php",
                "summary_max_chars": 220,
                "detail_max_chars": 1600,
                "image_size": 640,
            }
        ).search("鱼")

        self.assertEqual(result.title, "鱼")
        self.assertIn("鱼可以通过钓鱼技能获得。", result.detail)
        self.assertIn("不同鱼类", result.detail)
        self.assertNotIn("忽略信息框", result.detail)
        self.assertEqual(result.summary, "鱼可以通过钓鱼技能获得。")
        self.assertEqual(result.image_url, "https://stardewvalleywiki.com/mediawiki/images/Fish.gif")

    def test_mc_forward_nodes_include_link_detail_and_image(self) -> None:
        result = McWikiResult(
            title="苦力怕",
            summary="苦力怕是一种敌对生物。",
            detail="苦力怕是一种敌对生物。\n\n它会接近玩家并爆炸。",
            url="https://zh.minecraft.wiki/w/苦力怕",
            image_url="https://zh.minecraft.wiki/images/Creeper.png",
        )

        with patch.object(mc_wiki_plugin, "msg", side_effect=_default_message):
            nodes = mc_wiki_plugin._build_forward_nodes("123456", result)

        self.assertEqual(len(nodes), 3)
        self.assertIn("https://zh.minecraft.wiki/w/苦力怕", str(nodes[0].data["content"]))
        self.assertIn("它会接近玩家并爆炸", str(nodes[1].data["content"]))
        self.assertIn("苦力怕 主图", str(nodes[2].data["content"]))
        self.assertIn("[CQ:image", str(nodes[2].data["content"]))

    def test_stardew_forward_nodes_skip_missing_image(self) -> None:
        result = stardew_wiki_plugin.StardewWikiResult(
            title="鱼",
            summary="鱼可以通过钓鱼技能获得。",
            detail="鱼可以通过钓鱼技能获得。",
            url="https://zh.stardewvalleywiki.com/鱼",
            image_url="",
        )

        with patch.object(stardew_wiki_plugin, "msg", side_effect=_default_message):
            nodes = stardew_wiki_plugin._build_forward_nodes("123456", result)

        self.assertEqual(len(nodes), 2)
        self.assertIn("https://zh.stardewvalleywiki.com/鱼", str(nodes[0].data["content"]))
        self.assertIn("鱼可以通过钓鱼技能获得。", str(nodes[1].data["content"]))


if __name__ == "__main__":
    unittest.main()
