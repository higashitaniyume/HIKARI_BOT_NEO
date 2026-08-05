"""DeepSeek Responses API 协议测试。

覆盖: 端点拼接、payload 结构（instructions/input/max_output_tokens/reasoning）、
服务端内置 web_search 工具、web_search_call 原样回传、函数工具循环、
400 降级、wiki 预取的 input item 注入、搜索模式选择。
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

import plugins.mc_wiki as mc_wiki_plugin
from plugins.aiagent import client as aiagent_client
from plugins.aiagent import config as aiagent_config
from plugins.aiagent import responses_client as responses_client
from plugins.aiagent.tools import registry as tool_registry
from plugins.aiagent.tools import search as search_tool
from plugins.mc_wiki.api import McWikiResult


class FakeResponse:
    def __init__(self, status_code: int, data: dict[str, object], text: str = "") -> None:
        self.status_code = status_code
        self._data = data
        self.text = text

    def json(self) -> dict[str, object]:
        return self._data


def _message_item(message_id: str, text: str) -> dict[str, object]:
    return {
        "type": "message",
        "id": message_id,
        "role": "assistant",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


class PlainResponsesAsyncClient:
    post_payloads: list[dict[str, object]] = []

    def __init__(self, *args, **kwargs) -> None:
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, object]):
        PlainResponsesAsyncClient.post_payloads.append(json)
        return FakeResponse(200, {"output": [_message_item("msg_1", "普通回复")], "output_text": "普通回复"})


class ToolLoopResponsesAsyncClient:
    """第 1 轮返回 web_search_call + function_call，第 2 轮返回最终消息。"""

    post_payloads: list[dict[str, object]] = []

    def __init__(self, *args, **kwargs) -> None:
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, object]):
        ToolLoopResponsesAsyncClient.post_payloads.append(json)
        if len(ToolLoopResponsesAsyncClient.post_payloads) == 1:
            return FakeResponse(
                200,
                {
                    "output": [
                        {
                            "type": "web_search_call",
                            "id": "ws_1",
                            "status": "completed",
                            "action": {"type": "search", "query": "HIKARI BOT"},
                        },
                        {
                            "type": "function_call",
                            "id": "fc_1",
                            "call_id": "call_1",
                            "name": "bot_help",
                            "arguments": "{}",
                            "status": "completed",
                        },
                    ],
                    "output_text": "",
                },
            )
        return FakeResponse(
            200,
            {"output": [_message_item("msg_2", "最终回复")], "output_text": "最终回复"},
        )


class DegradeResponsesAsyncClient:
    post_payloads: list[dict[str, object]] = []

    def __init__(self, *args, **kwargs) -> None:
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, object]):
        DegradeResponsesAsyncClient.post_payloads.append(json)
        if len(DegradeResponsesAsyncClient.post_payloads) == 1:
            return FakeResponse(400, {}, "unknown field: tools")
        return FakeResponse(200, {"output": [_message_item("msg_1", "降级回复")], "output_text": "降级回复"})


class WikiPrefetchResponsesAsyncClient:
    post_payloads: list[dict[str, object]] = []

    def __init__(self, *args, **kwargs) -> None:
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, object]):
        WikiPrefetchResponsesAsyncClient.post_payloads.append(json)
        return FakeResponse(
            200,
            {"output": [_message_item("msg_1", "综合回复")], "output_text": "综合回复"},
        )


def base_cfg(*, search_mode: str = "builtin", thinking_enabled: bool = True) -> dict[str, object]:
    return {
        "api": {"protocol": "responses"},
        "model": {
            "base_url": "https://api.deepseek.com",
            "api_key": "",
            "model": "deepseek-v4-flash",
            "temperature": 0.7,
            "top_p": 1.0,
            "max_tokens": 256,
            "timeout_seconds": 5,
            "proxy": "",
        },
        "thinking": {"enabled": thinking_enabled, "reasoning_effort": "high"},
        "tools": {
            "search": {
                "enabled": True,
                "mode": search_mode,
                "base_url": "http://searxng-core:8080",
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


class ResponsesEndpointTests(unittest.TestCase):
    def test_endpoint_normalizes_base_url_variants(self) -> None:
        self.assertEqual(
            responses_client.endpoint("https://api.deepseek.com"),
            "https://api.deepseek.com/responses",
        )
        # Chat Completions 风格的 /v1 base_url 也能正确拼接
        self.assertEqual(
            responses_client.endpoint("https://api.deepseek.com/v1"),
            "https://api.deepseek.com/responses",
        )
        self.assertEqual(
            responses_client.endpoint("https://api.deepseek.com/v1/"),
            "https://api.deepseek.com/responses",
        )
        self.assertEqual(
            responses_client.endpoint("https://api.deepseek.com/responses"),
            "https://api.deepseek.com/responses",
        )
        self.assertEqual(responses_client.endpoint(""), "https://api.deepseek.com/responses")

    def test_api_protocol_detection(self) -> None:
        self.assertEqual(aiagent_config.api_protocol({"api": {"protocol": "responses"}}), "responses")
        self.assertEqual(aiagent_config.api_protocol({"api": {"protocol": "chat_completions"}}), "chat_completions")
        # 缺失 / 非法值回退到 responses
        self.assertEqual(aiagent_config.api_protocol({}), "responses")
        self.assertEqual(aiagent_config.api_protocol({"api": {"protocol": "nonsense"}}), "responses")


class ResponsesClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_plain_chat_sends_instructions_and_input_items(self) -> None:
        PlainResponsesAsyncClient.post_payloads = []

        # 首条 system → instructions；其余消息 → input items；问候语不触发工具
        messages = [
            {"role": "system", "content": "你是测试助手。"},
            {"role": "system", "content": "【当前时间】2026年8月5日"},
            {"role": "user", "content": "你好"},
        ]
        with patch.object(responses_client.httpx, "AsyncClient", PlainResponsesAsyncClient):
            reply = await responses_client.request_response_completion(base_cfg(), messages)

        self.assertEqual(reply, "普通回复")
        payload = PlainResponsesAsyncClient.post_payloads[0]
        self.assertEqual(payload["instructions"], "你是测试助手。")
        self.assertEqual(payload["max_output_tokens"], 256)
        # 思考模式 → reasoning.effort（而非 chat completions 的 thinking 字段）
        self.assertEqual(payload["reasoning"], {"effort": "high"})

        input_items = payload["input"]
        assert isinstance(input_items, list)
        self.assertEqual(input_items[0], {"role": "system", "content": "【当前时间】2026年8月5日"})
        self.assertEqual(input_items[-1], {"role": "user", "content": "你好"})
        self.assertNotIn("tools", payload)

    async def test_plain_chat_with_reasoning_disabled_omits_reasoning(self) -> None:
        PlainResponsesAsyncClient.post_payloads = []

        with patch.object(responses_client.httpx, "AsyncClient", PlainResponsesAsyncClient):
            await responses_client.request_response_completion(
                base_cfg(thinking_enabled=False),
                [{"role": "system", "content": "你是测试助手。"}, {"role": "user", "content": "你好"}],
            )

        payload = PlainResponsesAsyncClient.post_payloads[0]
        self.assertNotIn("reasoning", payload)
        # 非思考模式仍发送温度参数
        self.assertIn("temperature", payload)

    async def test_builtin_search_tool_is_declared_in_responses_mode(self) -> None:
        tools = tool_registry.available_tools(base_cfg())
        self.assertIn({"type": "web_search"}, tools)
        self.assertNotIn("web_search", [t.get("function", {}).get("name") for t in tools if isinstance(t, dict)])

    async def test_searxng_mode_keeps_function_search_tool(self) -> None:
        tools = tool_registry.available_tools(base_cfg(search_mode="searxng"))
        names = {t["function"]["name"] for t in tools if isinstance(t.get("function"), dict)}
        self.assertIn(search_tool.TOOL_NAME, names)
        self.assertNotIn({"type": "web_search"}, tools)

    async def test_chat_protocol_with_builtin_mode_falls_back_to_searxng(self) -> None:
        cfg = base_cfg()
        assert isinstance(cfg, dict)
        cfg["api"] = {"protocol": "chat_completions"}
        tools = tool_registry.available_tools(cfg)
        names = {t["function"]["name"] for t in tools if isinstance(t.get("function"), dict)}
        self.assertIn(search_tool.TOOL_NAME, names)
        self.assertNotIn({"type": "web_search"}, tools)

    async def test_tool_loop_passes_web_search_call_back_and_runs_function(self) -> None:
        ToolLoopResponsesAsyncClient.post_payloads = []

        with patch.object(responses_client.httpx, "AsyncClient", ToolLoopResponsesAsyncClient):
            reply = await responses_client.request_response_completion(
                base_cfg(),
                [{"role": "system", "content": "你是测试助手。"}, {"role": "user", "content": "查一下 HIKARI BOT"}],
            )

        self.assertEqual(reply, "最终回复")
        self.assertEqual(len(ToolLoopResponsesAsyncClient.post_payloads), 2)

        first = ToolLoopResponsesAsyncClient.post_payloads[0]
        tools = first["tools"]
        assert isinstance(tools, list)
        self.assertIn({"type": "web_search"}, tools)

        second = ToolLoopResponsesAsyncClient.post_payloads[1]
        input_items = second["input"]
        assert isinstance(input_items, list)
        # web_search_call 原样回传，服务端自动恢复搜索结果
        self.assertIn(
            {
                "type": "web_search_call",
                "id": "ws_1",
                "status": "completed",
                "action": {"type": "search", "query": "HIKARI BOT"},
            },
            input_items,
        )
        # function_call 及其执行结果一并回传
        self.assertIn(
            {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "bot_help", "arguments": "{}", "status": "completed"},
            input_items,
        )
        output_item = next(item for item in input_items if isinstance(item, dict) and item.get("type") == "function_call_output")
        self.assertEqual(output_item["call_id"], "call_1")
        self.assertIn("以下是本机器人的功能文档列表", str(output_item["output"]))
        self.assertEqual(second["instructions"], "你是测试助手。")

    async def test_tool_unsupported_response_falls_back_to_plain_chat(self) -> None:
        DegradeResponsesAsyncClient.post_payloads = []

        with (
            patch.object(responses_client.httpx, "AsyncClient", DegradeResponsesAsyncClient),
            patch.object(responses_client.logger, "warning"),
        ):
            reply = await responses_client.request_response_completion(
                base_cfg(),
                [{"role": "system", "content": "你是测试助手。"}, {"role": "user", "content": "今天的新闻"}],
            )

        self.assertEqual(reply, "降级回复")
        self.assertEqual(len(DegradeResponsesAsyncClient.post_payloads), 2)
        self.assertIn("tools", DegradeResponsesAsyncClient.post_payloads[0])
        self.assertNotIn("tools", DegradeResponsesAsyncClient.post_payloads[1])

    async def test_mc_wiki_question_prefetches_function_call_items(self) -> None:
        WikiPrefetchResponsesAsyncClient.post_payloads = []

        with (
            patch.object(responses_client.httpx, "AsyncClient", WikiPrefetchResponsesAsyncClient),
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
            reply = await responses_client.request_response_completion(
                _cfg_with_plugin_tool("mc_wiki_search"),
                [{"role": "system", "content": "你是测试助手。"}, {"role": "user", "content": "mcwiki 苦力怕"}],
            )

        self.assertEqual(reply, "综合回复")
        wiki_search.assert_awaited_once_with("苦力怕")

        payload = WikiPrefetchResponsesAsyncClient.post_payloads[0]
        input_items = payload["input"]
        assert isinstance(input_items, list)
        call_item = next(item for item in input_items if isinstance(item, dict) and item.get("type") == "function_call")
        self.assertEqual(call_item["name"], "mc_wiki_search")
        self.assertEqual(json.loads(call_item["arguments"])["query"], "苦力怕")
        output_item = next(item for item in input_items if isinstance(item, dict) and item.get("type") == "function_call_output")
        self.assertEqual(output_item["call_id"], call_item["call_id"])
        # 内置搜索模式下不注入额外的 web_search 函数调用
        self.assertNotIn("web_search", [item.get("name") for item in input_items if isinstance(item, dict)])


if __name__ == "__main__":
    unittest.main()
