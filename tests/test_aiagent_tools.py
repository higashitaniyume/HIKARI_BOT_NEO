from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import plugins.aiagent as aiagent
from plugins.aiagent import client as aiagent_client
from plugins.aiagent.tools import registry as tool_registry
from plugins.aiagent.tools import search as search_tool


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
