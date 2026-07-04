from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

import plugins.mc_wiki as mc_wiki_plugin
from core.ai_tool_registry import AIToolContext, register_ai_tool
from plugins.aiagent.tools import registry as aiagent_tools
from plugins.mc_wiki.api import McWikiResult


class _FakeEvent:
    def get_user_id(self) -> str:
        return "42"


@register_ai_tool(
    "unit_ai_readonly",
    plugin_name="unit",
    description="Unit test readonly tool.",
    parameters={
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "additionalProperties": False,
    },
)
async def _unit_ai_readonly(context: AIToolContext, arguments: dict[str, object]) -> dict[str, object]:
    user_id = context.event.get_user_id() if context.event is not None else ""
    return {"value": arguments.get("value"), "user_id": user_id}


@register_ai_tool(
    "unit_ai_side_effect",
    plugin_name="unit",
    description="Unit test side-effect tool.",
    parameters={
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    readonly=False,
)
async def _unit_ai_side_effect(context: AIToolContext, arguments: dict[str, object]) -> dict[str, object]:
    return {"ok": True}


def _cfg(plugin_tools: dict[str, object]) -> dict[str, object]:
    return {
        "tools": {
            "search": {"enabled": False},
            "files": {"enabled": False},
            "plugin_tools": plugin_tools,
            "max_tool_rounds": 2,
        }
    }


def _call(name: str, arguments: str = "{}") -> dict[str, object]:
    return {
        "id": f"call_{name}",
        "function": {
            "name": name,
            "arguments": arguments,
        },
    }


class AIAgentPluginToolTests(unittest.IsolatedAsyncioTestCase):
    def test_plugin_tools_respect_readonly_and_name_filters(self) -> None:
        base = {
            "enabled": True,
            "enabled_names": ["unit_ai_readonly", "unit_ai_side_effect"],
            "disabled_names": [],
            "allow_side_effects": False,
        }

        readonly_names = {tool["function"]["name"] for tool in aiagent_tools.available_tools(_cfg(base))}
        self.assertIn("unit_ai_readonly", readonly_names)
        self.assertNotIn("unit_ai_side_effect", readonly_names)

        allow_side_effects = dict(base)
        allow_side_effects["allow_side_effects"] = True
        all_names = {tool["function"]["name"] for tool in aiagent_tools.available_tools(_cfg(allow_side_effects))}
        self.assertIn("unit_ai_side_effect", all_names)

        disabled = dict(allow_side_effects)
        disabled["disabled_names"] = ["unit_ai_readonly"]
        filtered_names = {tool["function"]["name"] for tool in aiagent_tools.available_tools(_cfg(disabled))}
        self.assertNotIn("unit_ai_readonly", filtered_names)

    async def test_plugin_tool_execution_receives_context(self) -> None:
        result = await aiagent_tools.execute_tool_call(
            _cfg({"enabled": True, "enabled_names": ["unit_ai_readonly"], "allow_side_effects": False}),
            _call("unit_ai_readonly", "{\"value\":\"hello\"}"),
            AIToolContext(event=_FakeEvent()),
        )

        payload = json.loads(result["content"])
        self.assertEqual(payload["value"], "hello")
        self.assertEqual(payload["user_id"], "42")

    async def test_registered_plugin_tool_can_be_called_through_aiagent(self) -> None:
        cfg = {
            "enabled": True,
            "api_url": "https://zh.minecraft.wiki/api.php",
            "timeout": 12,
            "search_limit": 3,
            "summary_max_chars": 220,
            "proxy": "",
        }
        with (
            patch.object(mc_wiki_plugin, "get_config", Mock(return_value=cfg)),
            patch.object(
                mc_wiki_plugin.McWikiClient,
                "search",
                AsyncMock(
                    return_value=McWikiResult(
                        title="红石",
                        summary="红石是一种材料。",
                        detail="红石是一种材料，可用于制作红石电路。",
                        url="https://zh.minecraft.wiki/w/红石",
                        image_url="https://zh.minecraft.wiki/images/Redstone.png",
                    )
                ),
            ) as search_mock,
        ):
            result = await aiagent_tools.execute_tool_call(
                _cfg({"enabled": True, "enabled_names": ["mc_wiki_search"], "allow_side_effects": False}),
                _call("mc_wiki_search", "{\"query\":\"红石\"}"),
            )

        payload = json.loads(result["content"])
        self.assertEqual(payload["results"][0]["title"], "红石")
        self.assertEqual(payload["results"][0]["detail"], "红石是一种材料，可用于制作红石电路。")
        self.assertEqual(payload["results"][0]["url"], "https://zh.minecraft.wiki/w/红石")
        self.assertEqual(payload["results"][0]["image_url"], "https://zh.minecraft.wiki/images/Redstone.png")
        search_mock.assert_awaited_once_with("红石")


if __name__ == "__main__":
    unittest.main()
