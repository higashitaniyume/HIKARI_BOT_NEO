from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import plugins.aiagent as aiagent
import plugins.mc_wiki as mc_wiki_plugin
import plugins.stardew_wiki as stardew_wiki_plugin
import plugins.sts2_wiki as sts2_wiki_plugin
from plugins.aiagent import client as aiagent_client
from plugins.aiagent.tools import registry as tool_registry
from plugins.aiagent.tools import search as search_tool
from plugins.mc_wiki.api import McWikiResult
from plugins.stardew_wiki.api import StardewWikiResult
from plugins.sts2_wiki.models import Sts2WikiResult


@contextmanager
def temporary_cwd(path: Path):
    old_cwd = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old_cwd)


class FakeResponse:
    def __init__(self, status_code: int, data: dict[str, object], text: str = "") -> None:
        self.status_code = status_code
        self._data = data
        self.text = text

    def json(self) -> dict[str, object]:
        return self._data


class ToolCallingAsyncClient:
    post_payloads: list[dict[str, object]] = []
    get_calls: list[tuple[str, dict[str, object]]] = []

    def __init__(self, *args, **kwargs) -> None:
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, object]):
        ToolCallingAsyncClient.post_payloads.append(json)
        if len(ToolCallingAsyncClient.post_payloads) == 1:
            return FakeResponse(
                200,
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "web_search",
                                            "arguments": "{\"query\":\"HIKARI BOT\",\"max_results\":2}",
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
            )
        return FakeResponse(200, {"choices": [{"message": {"role": "assistant", "content": "最终回复"}}]})

    async def get(self, url: str, *, params: dict[str, object]):
        ToolCallingAsyncClient.get_calls.append((url, params))
        return FakeResponse(
            200,
            {
                "results": [
                    {
                        "title": "Result title",
                        "url": "https://example.com/result",
                        "content": "Result snippet",
                        "engine": "example",
                    }
                ]
            },
        )


class WikiPriorityAsyncClient:
    post_payloads: list[dict[str, object]] = []
    get_calls: list[tuple[str, dict[str, object]]] = []

    def __init__(self, *args, **kwargs) -> None:
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, object]):
        WikiPriorityAsyncClient.post_payloads.append(json)
        return FakeResponse(200, {"choices": [{"message": {"role": "assistant", "content": "综合回复"}}]})

    async def get(self, url: str, *, params: dict[str, object]):
        WikiPriorityAsyncClient.get_calls.append((url, params))
        return FakeResponse(
            200,
            {
                "results": [
                    {
                        "title": "Search title",
                        "url": "https://example.com/search",
                        "content": "Search snippet",
                        "engine": "example",
                    }
                ]
            },
        )


class PlainAsyncClient:
    post_payloads: list[dict[str, object]] = []

    def __init__(self, *args, **kwargs) -> None:
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, object]):
        PlainAsyncClient.post_payloads.append(json)
        return FakeResponse(200, {"choices": [{"message": {"role": "assistant", "content": "普通回复"}}]})


class ToolUnsupportedAsyncClient:
    post_payloads: list[dict[str, object]] = []

    def __init__(self, *args, **kwargs) -> None:
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, object]):
        ToolUnsupportedAsyncClient.post_payloads.append(json)
        if len(ToolUnsupportedAsyncClient.post_payloads) == 1:
            return FakeResponse(400, {}, "unknown field: tools")
        return FakeResponse(200, {"choices": [{"message": {"role": "assistant", "content": "降级回复"}}]})


def base_cfg(*, search_enabled: bool = True, files_enabled: bool = False) -> dict[str, object]:
    return {
        "model": {
            "base_url": "https://api.example.test/v1",
            "api_key": "",
            "model": "test-model",
            "temperature": 0.7,
            "top_p": 1.0,
            "max_tokens": 256,
            "timeout_seconds": 5,
            "proxy": "",
        },
        "tools": {
            "search": {
                "enabled": search_enabled,
                "base_url": "http://searxng-core:8080",
                "timeout_seconds": 5,
                "max_results": 5,
                "safesearch": 1,
                "language": "auto",
                "categories": "general",
            },
            "files": {
                "enabled": files_enabled,
                "max_read_chars": 20000,
                "max_write_chars": 20000,
            },
            "max_tool_rounds": 2,
        },
    }


def _cfg_with_plugin_tool(tool_name: str) -> dict[str, object]:
    cfg = base_cfg()
    tools = cfg["tools"]
    assert isinstance(tools, dict)
    tools["plugin_tools"] = {
        "enabled": True,
        "allow_side_effects": False,
        "enabled_names": [tool_name],
        "disabled_names": [],
    }
    return cfg


class AIAgentToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_completion_executes_search_tool_call(self) -> None:
        ToolCallingAsyncClient.post_payloads = []
        ToolCallingAsyncClient.get_calls = []

        with (
            patch.object(aiagent_client.httpx, "AsyncClient", ToolCallingAsyncClient),
            patch.object(search_tool.httpx, "AsyncClient", ToolCallingAsyncClient),
        ):
            reply = await aiagent._request_chat_completion(
                base_cfg(),
                [{"role": "user", "content": "查一下 HIKARI BOT"}],
            )

        self.assertEqual(reply, "最终回复")
        self.assertEqual(len(ToolCallingAsyncClient.post_payloads), 2)
        self.assertIn("tools", ToolCallingAsyncClient.post_payloads[0])
        self.assertEqual(ToolCallingAsyncClient.get_calls[0][0], "http://searxng-core:8080/search")
        self.assertEqual(ToolCallingAsyncClient.get_calls[0][1]["q"], "HIKARI BOT")

        second_messages = ToolCallingAsyncClient.post_payloads[1]["messages"]
        assert isinstance(second_messages, list)
        tool_messages = [message for message in second_messages if isinstance(message, dict) and message.get("role") == "tool"]
        self.assertEqual(len(tool_messages), 1)
        tool_payload = json.loads(str(tool_messages[0]["content"]))
        self.assertEqual(tool_payload["results"][0]["title"], "Result title")

    async def test_mc_wiki_question_prefetches_wiki_tool_before_web_search(self) -> None:
        WikiPriorityAsyncClient.post_payloads = []
        WikiPriorityAsyncClient.get_calls = []

        with (
            patch.object(aiagent_client.httpx, "AsyncClient", WikiPriorityAsyncClient),
            patch.object(search_tool.httpx, "AsyncClient", WikiPriorityAsyncClient),
            patch.object(
                mc_wiki_plugin.McWikiClient,
                "search",
                AsyncMock(
                    return_value=McWikiResult(
                        title="苦力怕",
                        summary="苦力怕是一种敌对生物。",
                        detail="苦力怕是一种敌对生物。",
                        url="https://zh.minecraft.wiki/w/苦力怕",
                        image_url="https://zh.minecraft.wiki/images/Creeper.png",
                    )
                ),
            ) as wiki_search,
        ):
            reply = await aiagent._request_chat_completion(
                _cfg_with_plugin_tool("mc_wiki_search"),
                [{"role": "user", "content": "mcwiki 苦力怕"}],
            )

        self.assertEqual(reply, "综合回复")
        wiki_search.assert_awaited_once_with("苦力怕")
        self.assertEqual(WikiPriorityAsyncClient.get_calls[0][1]["q"], "mcwiki 苦力怕")

        messages = WikiPriorityAsyncClient.post_payloads[0]["messages"]
        assert isinstance(messages, list)
        assistant_calls = next(message["tool_calls"] for message in messages if isinstance(message, dict) and message.get("role") == "assistant")
        self.assertEqual([call["function"]["name"] for call in assistant_calls], ["mc_wiki_search", "web_search"])
        tool_messages = [message for message in messages if isinstance(message, dict) and message.get("role") == "tool"]
        self.assertEqual([message["name"] for message in tool_messages], ["mc_wiki_search", "web_search"])

    async def test_stardew_wiki_question_prefetches_wiki_tool_before_web_search(self) -> None:
        WikiPriorityAsyncClient.post_payloads = []
        WikiPriorityAsyncClient.get_calls = []

        with (
            patch.object(aiagent_client.httpx, "AsyncClient", WikiPriorityAsyncClient),
            patch.object(search_tool.httpx, "AsyncClient", WikiPriorityAsyncClient),
            patch.object(
                stardew_wiki_plugin.StardewWikiClient,
                "search",
                AsyncMock(
                    return_value=StardewWikiResult(
                        title="鱼",
                        summary="鱼可以通过钓鱼技能获得。",
                        detail="鱼可以通过钓鱼技能获得。",
                        url="https://zh.stardewvalleywiki.com/鱼",
                        image_url="https://stardewvalleywiki.com/mediawiki/images/Fish.gif",
                    )
                ),
            ) as wiki_search,
        ):
            reply = await aiagent._request_chat_completion(
                _cfg_with_plugin_tool("stardew_wiki_search"),
                [{"role": "user", "content": "星露谷物语wiki 鱼"}],
            )

        self.assertEqual(reply, "综合回复")
        wiki_search.assert_awaited_once_with("鱼")
        self.assertEqual(WikiPriorityAsyncClient.get_calls[0][1]["q"], "星露谷物语wiki 鱼")

        messages = WikiPriorityAsyncClient.post_payloads[0]["messages"]
        assert isinstance(messages, list)
        assistant_calls = next(message["tool_calls"] for message in messages if isinstance(message, dict) and message.get("role") == "assistant")
        self.assertEqual([call["function"]["name"] for call in assistant_calls], ["stardew_wiki_search", "web_search"])
        tool_messages = [message for message in messages if isinstance(message, dict) and message.get("role") == "tool"]
        self.assertEqual([message["name"] for message in tool_messages], ["stardew_wiki_search", "web_search"])

    async def test_sts2_wiki_question_prefetches_wiki_tool_before_web_search(self) -> None:
        WikiPriorityAsyncClient.post_payloads = []
        WikiPriorityAsyncClient.get_calls = []

        with (
            patch.object(aiagent_client.httpx, "AsyncClient", WikiPriorityAsyncClient),
            patch.object(search_tool.httpx, "AsyncClient", WikiPriorityAsyncClient),
            patch.object(
                sts2_wiki_plugin,
                "get_config",
                return_value={
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
                },
            ),
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
                    )
                ),
            ) as wiki_search,
        ):
            reply = await aiagent._request_chat_completion(
                _cfg_with_plugin_tool("sts2_wiki_search"),
                [{"role": "user", "content": "sts2 Strike"}],
            )

        self.assertEqual(reply, "综合回复")
        wiki_search.assert_awaited_once_with("Strike")
        self.assertEqual(WikiPriorityAsyncClient.get_calls[0][1]["q"], "sts2 Strike")

        messages = WikiPriorityAsyncClient.post_payloads[0]["messages"]
        assert isinstance(messages, list)
        assistant_calls = next(message["tool_calls"] for message in messages if isinstance(message, dict) and message.get("role") == "assistant")
        self.assertEqual([call["function"]["name"] for call in assistant_calls], ["sts2_wiki_search", "web_search"])
        tool_messages = [message for message in messages if isinstance(message, dict) and message.get("role") == "tool"]
        self.assertEqual([message["name"] for message in tool_messages], ["sts2_wiki_search", "web_search"])

    async def test_disabled_search_tool_is_not_sent_to_model(self) -> None:
        PlainAsyncClient.post_payloads = []

        with patch.object(aiagent_client.httpx, "AsyncClient", PlainAsyncClient):
            reply = await aiagent._request_chat_completion(
                base_cfg(search_enabled=False),
                [{"role": "user", "content": "你好"}],
            )

        self.assertEqual(reply, "普通回复")
        self.assertEqual(len(PlainAsyncClient.post_payloads), 1)
        self.assertNotIn("tools", PlainAsyncClient.post_payloads[0])

    async def test_tool_unsupported_response_falls_back_to_plain_chat(self) -> None:
        ToolUnsupportedAsyncClient.post_payloads = []

        with (
            patch.object(aiagent_client.httpx, "AsyncClient", ToolUnsupportedAsyncClient),
            patch.object(aiagent_client.logger, "warning"),
        ):
            reply = await aiagent._request_chat_completion(
                base_cfg(),
                [{"role": "user", "content": "你好"}],
            )

        self.assertEqual(reply, "降级回复")
        self.assertEqual(len(ToolUnsupportedAsyncClient.post_payloads), 2)
        self.assertIn("tools", ToolUnsupportedAsyncClient.post_payloads[0])
        self.assertNotIn("tools", ToolUnsupportedAsyncClient.post_payloads[1])

    def test_file_tools_are_declared_when_enabled(self) -> None:
        tools = aiagent._available_tools(base_cfg(search_enabled=False, files_enabled=True))
        tool_names = {tool["function"]["name"] for tool in tools}
        self.assertEqual(tool_names, {"read_persona_resource", "read_user_file", "write_user_file"})

    async def test_file_tools_respect_botdata_and_userdata_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "BotData" / "agent_personas" / "nuwa").mkdir(parents=True)
            (root / "BotData" / "plugin_configs").mkdir(parents=True)
            (root / "UserData").mkdir()
            (root / "BotData" / "config.json").write_text("{\"bot\":true}", encoding="utf-8")
            (root / "BotData" / "plugin_configs" / "aiagent.json").write_text("{\"enabled\":true}", encoding="utf-8")
            (root / "BotData" / "agent_personas" / "nuwa" / "tone.md").write_text("warm tone", encoding="utf-8")
            (root / "UserData" / "note.txt").write_text("hello", encoding="utf-8")

            with temporary_cwd(root), patch.object(tool_registry.logger, "warning"):
                cfg = base_cfg(search_enabled=False, files_enabled=True)
                persona_result = await aiagent._execute_tool_call(
                    cfg,
                    {
                        "id": "read_persona",
                        "function": {"name": "read_persona_resource", "arguments": "{\"path\":\"nuwa/tone.md\"}"},
                    },
                )
                blocked_config_result = await aiagent._execute_tool_call(
                    cfg,
                    {
                        "id": "read_config",
                        "function": {"name": "read_persona_resource", "arguments": "{\"path\":\"../config.json\"}"},
                    },
                )
                blocked_plugin_config_result = await aiagent._execute_tool_call(
                    cfg,
                    {
                        "id": "read_plugin_config",
                        "function": {
                            "name": "read_persona_resource",
                            "arguments": "{\"path\":\"../plugin_configs/aiagent.json\"}",
                        },
                    },
                )
                user_result = await aiagent._execute_tool_call(
                    cfg,
                    {
                        "id": "read_user",
                        "function": {"name": "read_user_file", "arguments": "{\"path\":\"note.txt\"}"},
                    },
                )
                write_result = await aiagent._execute_tool_call(
                    cfg,
                    {
                        "id": "write_user",
                        "function": {
                            "name": "write_user_file",
                            "arguments": "{\"path\":\"notes/out.txt\",\"content\":\"saved\",\"mode\":\"overwrite\"}",
                        },
                    },
                )
                escape_result = await aiagent._execute_tool_call(
                    cfg,
                    {
                        "id": "escape",
                        "function": {
                            "name": "write_user_file",
                            "arguments": "{\"path\":\"../BotData/config.json\",\"content\":\"bad\"}",
                        },
                    },
                )

            persona_payload = json.loads(persona_result["content"])
            blocked_config_payload = json.loads(blocked_config_result["content"])
            blocked_plugin_config_payload = json.loads(blocked_plugin_config_result["content"])
            user_payload = json.loads(user_result["content"])
            write_payload = json.loads(write_result["content"])
            escape_payload = json.loads(escape_result["content"])

            self.assertEqual(persona_payload["content"], "warm tone")
            self.assertIn("outside allowed directory", blocked_config_payload["error"])
            self.assertIn("outside allowed directory", blocked_plugin_config_payload["error"])
            self.assertEqual(user_payload["content"], "hello")
            self.assertTrue(write_payload["ok"])
            self.assertEqual((root / "UserData" / "notes" / "out.txt").read_text(encoding="utf-8"), "saved")
            self.assertIn("outside allowed directory", escape_payload["error"])


if __name__ == "__main__":
    unittest.main()
