"""DeepSeek 官方 Search API（``web_search`` 的 builtin 模式）测试。

覆盖：端点归一、请求字段（count / ori_question / need_content）、结果裁剪、
408/429/5xx 重试、4xx 不重试、缺 Key 不发请求、非 DeepSeek 端点回退 SearXNG。
"""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from plugins.aiagent.tools import search as search_tool


class FakeResponse:
    def __init__(self, status_code: int, data: dict[str, Any] | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._data = data or {}
        self.text = text

    def json(self) -> dict[str, Any]:
        return self._data


class FakeSearchClient:
    """记录 post/get 请求，按 responses 队列返回预置响应。"""

    posts: list[dict[str, Any]] = []
    gets: list[dict[str, Any]] = []
    responses: list[FakeResponse] = []
    get_response: FakeResponse | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.kwargs = kwargs

    async def __aenter__(self) -> "FakeSearchClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> FakeResponse:
        FakeSearchClient.posts.append({"url": url, "headers": headers, "json": json})
        if FakeSearchClient.responses:
            return FakeSearchClient.responses.pop(0)
        return FakeResponse(200, {"results": []})

    async def get(self, url: str, params: dict[str, Any]) -> FakeResponse:
        FakeSearchClient.gets.append({"url": url, "params": params})
        return FakeSearchClient.get_response or FakeResponse(200, {"results": []})


class FakeEvent:
    def __init__(self, text: str) -> None:
        self._text = text

    def get_plaintext(self) -> str:
        return self._text


class FakeContext:
    def __init__(self, text: str) -> None:
        self.event = FakeEvent(text)


def _cfg(
    *,
    base_url: str = "https://api.deepseek.com",
    api_key: str = "sk-test",
    mode: str = "builtin",
    **search_overrides: Any,
) -> dict[str, Any]:
    search_cfg: dict[str, Any] = {
        "enabled": True,
        "mode": mode,
        "base_url": "http://searxng-core:8080",
        "timeout_seconds": 5,
        "max_results": 5,
        "need_content": False,
        "safesearch": 1,
        "language": "auto",
        "categories": "general",
    }
    search_cfg.update(search_overrides)
    return {
        "model": {
            "base_url": base_url,
            "api_key": api_key,
            "model": "deepseek-flash",
            "proxy": "",
        },
        "tools": {"search": search_cfg},
    }


def _result(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "rerank_score": 2.4,
        "title": "DeepSeek V4 发布",
        "url": "https://example.com/v4",
        "snippet": "短摘要",
        "summary": "长摘要" * 10,
        "content": "网页正文" * 100,
        "site_name": "Example News",
        "date": "2026-06-01T00:00:00+00:00",
        "authority": 3,
    }
    item.update(overrides)
    return item


class SearchEndpointTests(unittest.TestCase):
    def test_endpoint_normalizes_base_url_variants(self) -> None:
        expected = "https://api.deepseek.com/v1/tools/search"
        for base in (
            "https://api.deepseek.com",
            "https://api.deepseek.com/",
            "https://api.deepseek.com/v1",
            "https://api.deepseek.com/v1/",
            "https://api.deepseek.com/v1/chat/completions",
            "https://api.deepseek.com/responses",
            "https://api.deepseek.com/v1/tools/search",
        ):
            self.assertEqual(search_tool.deepseek_endpoint(base), expected, base)
        self.assertEqual(search_tool.deepseek_endpoint(""), expected)

    def test_definition_matches_backend(self) -> None:
        deepseek = search_tool.definition(_cfg())
        properties = deepseek["function"]["parameters"]["properties"]
        self.assertIn("need_content", properties)
        self.assertIn("site:", properties["query"]["description"])

        searxng = search_tool.definition(_cfg(mode="searxng"))
        searxng_properties = searxng["function"]["parameters"]["properties"]
        self.assertIn("categories", searxng_properties)
        self.assertNotIn("need_content", searxng_properties)


class DeepSeekSearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        FakeSearchClient.posts = []
        FakeSearchClient.gets = []
        FakeSearchClient.responses = []
        FakeSearchClient.get_response = None

    async def test_request_uses_documented_fields(self) -> None:
        FakeSearchClient.responses = [FakeResponse(200, {"results": [_result()]})]

        with patch.object(search_tool.httpx, "AsyncClient", FakeSearchClient):
            output = await search_tool.execute(
                _cfg(),
                {"query": "DeepSeek V4", "max_results": 3, "need_content": True},
                FakeContext("今天 DeepSeek 发布了吗"),
            )

        self.assertEqual(len(FakeSearchClient.posts), 1)
        request = FakeSearchClient.posts[0]
        self.assertEqual(request["url"], "https://api.deepseek.com/v1/tools/search")
        self.assertEqual(request["headers"]["Authorization"], "Bearer sk-test")
        self.assertEqual(
            request["json"],
            {
                "query": "DeepSeek V4",
                "count": 3,
                "need_content": True,
                "ori_question": "今天 DeepSeek 发布了吗",
            },
        )

        payload = json.loads(output)
        result = payload["results"][0]
        self.assertEqual(result["title"], "DeepSeek V4 发布")
        self.assertEqual(result["score"], 2.4)
        self.assertEqual(result["authority"], 3)
        self.assertEqual(result["site"], "Example News")
        self.assertEqual(result["date"], "2026-06-01T00:00:00+00:00")
        self.assertIn("content", result)
        self.assertEqual(result["summary"], "长摘要" * 10)

    async def test_summary_falls_back_to_snippet_without_content(self) -> None:
        FakeSearchClient.responses = [
            FakeResponse(200, {"results": [_result(summary="", content="正文")]})
        ]

        with patch.object(search_tool.httpx, "AsyncClient", FakeSearchClient):
            output = await search_tool.execute(_cfg(), {"query": "x"})

        result = json.loads(output)["results"][0]
        self.assertEqual(result["summary"], "短摘要")
        self.assertNotIn("content", result)
        request = FakeSearchClient.posts[0]
        self.assertFalse(request["json"]["need_content"])
        self.assertNotIn("ori_question", request["json"])

    async def test_query_is_truncated_to_api_limit(self) -> None:
        FakeSearchClient.responses = [FakeResponse(200, {"results": []})]

        with patch.object(search_tool.httpx, "AsyncClient", FakeSearchClient):
            await search_tool.execute(_cfg(), {"query": "x" * 200})

        self.assertEqual(len(FakeSearchClient.posts[0]["json"]["query"]), 128)

    async def test_retries_429_then_succeeds(self) -> None:
        FakeSearchClient.responses = [
            FakeResponse(429, {"error": {"message": "qps limit"}}),
            FakeResponse(200, {"results": [_result()]}),
        ]

        with (
            patch.object(search_tool.httpx, "AsyncClient", FakeSearchClient),
            patch.object(search_tool.asyncio, "sleep", AsyncMock()) as sleep,
        ):
            output = await search_tool.execute(_cfg(), {"query": "x"})

        self.assertEqual(len(FakeSearchClient.posts), 2)
        sleep.assert_awaited_once()
        self.assertIn("results", json.loads(output))

    async def test_400_is_not_retried_and_reports_message(self) -> None:
        FakeSearchClient.responses = [
            FakeResponse(400, {"error": {"message": "'count' must be between 1 and 50"}})
        ]

        with patch.object(search_tool.httpx, "AsyncClient", FakeSearchClient):
            output = await search_tool.execute(_cfg(), {"query": "x"})

        self.assertEqual(len(FakeSearchClient.posts), 1)
        payload = json.loads(output)
        self.assertIn("HTTP 400", payload["error"])
        self.assertEqual(payload["detail"], "'count' must be between 1 and 50")

    async def test_missing_api_key_returns_error_without_request(self) -> None:
        with patch.object(search_tool.httpx, "AsyncClient", FakeSearchClient):
            output = await search_tool.execute(_cfg(api_key=""), {"query": "x"})

        self.assertEqual(FakeSearchClient.posts, [])
        self.assertIn("API Key", json.loads(output)["error"])

    async def test_non_deepseek_endpoint_falls_back_to_searxng(self) -> None:
        FakeSearchClient.get_response = FakeResponse(
            200, {"results": [{"title": "T", "url": "https://a.test", "content": "C"}]}
        )

        with patch.object(search_tool.httpx, "AsyncClient", FakeSearchClient):
            output = await search_tool.execute(
                _cfg(base_url="https://api.example.test/v1"), {"query": "x"}
            )

        self.assertEqual(FakeSearchClient.posts, [])
        self.assertEqual(FakeSearchClient.gets[0]["url"], "http://searxng-core:8080/search")
        self.assertEqual(json.loads(output)["results"][0]["title"], "T")


if __name__ == "__main__":
    unittest.main()
