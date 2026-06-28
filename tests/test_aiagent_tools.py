from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import plugins.aiagent as aiagent


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


def base_cfg(*, search_enabled: bool = True) -> dict[str, object]:
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
            "max_tool_rounds": 2,
        },
    }


class AIAgentToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_completion_executes_search_tool_call(self) -> None:
        ToolCallingAsyncClient.post_payloads = []
        ToolCallingAsyncClient.get_calls = []

        with patch.object(aiagent.httpx, "AsyncClient", ToolCallingAsyncClient):
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

        with patch.object(aiagent.httpx, "AsyncClient", PlainAsyncClient):
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
            patch.object(aiagent.httpx, "AsyncClient", ToolUnsupportedAsyncClient),
            patch.object(aiagent.logger, "warning"),
        ):
            reply = await aiagent._request_chat_completion(
                base_cfg(),
                [{"role": "user", "content": "你好"}],
            )

        self.assertEqual(reply, "降级回复")
        self.assertEqual(len(ToolUnsupportedAsyncClient.post_payloads), 2)
        self.assertIn("tools", ToolUnsupportedAsyncClient.post_payloads[0])
        self.assertNotIn("tools", ToolUnsupportedAsyncClient.post_payloads[1])


if __name__ == "__main__":
    unittest.main()
