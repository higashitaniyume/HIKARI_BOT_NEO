"""AI Agent 双引擎搜索（hybrid 模式）单元测试。

覆盖：
- 双后端并发请求
- URL 规范化与去重，SearXNG 的 content 作为 DeepSeek 的补充
- 单边失败降级（SearXNG 403/超时，或 DeepSeek 400/500）
- 无 API Key 自动降级为仅使用 SearXNG 且不报错误
- 两边均失败时返回结构化错误
- 工具定义包含两边参数
"""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import patch

from plugins.aiagent.tools import search as search_tool


class FakeResponse:
    def __init__(self, status_code: int, data: dict[str, Any] | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._data = data or {}
        self.text = text

    def json(self) -> dict[str, Any]:
        return self._data


class FakeHybridClient:
    posts: list[dict[str, Any]] = []
    gets: list[dict[str, Any]] = []
    post_response: FakeResponse | None = None
    get_response: FakeResponse | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.kwargs = kwargs

    async def __aenter__(self) -> "FakeHybridClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> FakeResponse:
        FakeHybridClient.posts.append({"url": url, "headers": headers, "json": json})
        if FakeHybridClient.post_response is not None:
            return FakeHybridClient.post_response
        return FakeResponse(200, {"results": []})

    async def get(self, url: str, params: dict[str, Any]) -> FakeResponse:
        FakeHybridClient.gets.append({"url": url, "params": params})
        if FakeHybridClient.get_response is not None:
            return FakeHybridClient.get_response
        return FakeResponse(200, {"results": []})


def _cfg(
    *,
    base_url: str = "https://api.deepseek.com",
    api_key: str = "sk-hybrid-test",
    mode: str = "hybrid",
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


class HybridSearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        FakeHybridClient.posts = []
        FakeHybridClient.gets = []
        FakeHybridClient.post_response = None
        FakeHybridClient.get_response = None

    def test_hybrid_definition(self) -> None:
        defn = search_tool.definition(_cfg(mode="hybrid"))
        props = defn["function"]["parameters"]["properties"]
        self.assertIn("query", props)
        self.assertIn("max_results", props)
        self.assertIn("need_content", props)
        self.assertIn("categories", props)
        self.assertIn("language", props)
        self.assertIn("time_range", props)

    async def test_concurrent_both_succeed_and_deduplicate(self) -> None:
        # DeepSeek 返回 2 条，其中一条与 SearXNG URL 重复
        FakeHybridClient.post_response = FakeResponse(
            200,
            {
                "results": [
                    {
                        "title": "DeepSeek Title 1",
                        "url": "https://example.com/article1/",
                        "summary": "DS Summary 1",
                        "rerank_score": 2.5,
                        "authority": 3,
                    },
                    {
                        "title": "DeepSeek Title 2",
                        "url": "https://www.example.org/page",
                        "summary": "DS Summary 2",
                        "rerank_score": 2.0,
                    },
                ]
            },
        )
        # SearXNG 返回 2 条，其中一条是 example.com/article1 (URL 大小写/带斜杠)，另一条是独立结果
        FakeHybridClient.get_response = FakeResponse(
            200,
            {
                "results": [
                    {
                        "title": "SX Title 1",
                        "url": "http://EXAMPLE.COM/article1",
                        "content": "SX Content 1 (supplemented)",
                    },
                    {
                        "title": "SX Title 3",
                        "url": "https://searxng-only.com/news",
                        "content": "SX Content 3",
                    },
                ]
            },
        )

        with patch.object(search_tool.httpx, "AsyncClient", FakeHybridClient):
            output = await search_tool.execute(_cfg(), {"query": "test query", "max_results": 5})

        self.assertEqual(len(FakeHybridClient.posts), 1)
        self.assertEqual(len(FakeHybridClient.gets), 1)

        payload = json.loads(output)
        self.assertEqual(payload["query"], "test query")
        results = payload["results"]
        # 去重后应为 3 条：article1 (DS+SX补充), page (DS), news (SX)
        self.assertEqual(len(results), 3)

        # 检查 article1 补充了 SearXNG 的 content
        match_article1 = next(r for r in results if "article1" in r["url"])
        self.assertEqual(match_article1["source"], "deepseek")
        self.assertEqual(match_article1["summary"], "DS Summary 1")
        self.assertEqual(match_article1["content"], "SX Content 1 (supplemented)")

        # 检查 sources 统计
        self.assertEqual(payload["sources"]["deepseek"], 2)
        self.assertEqual(payload["sources"]["searxng"], 1)
        self.assertNotIn("errors", payload)

    async def test_searxng_403_falls_back_to_deepseek_with_error_noted(self) -> None:
        FakeHybridClient.post_response = FakeResponse(
            200,
            {
                "results": [
                    {
                        "title": "DS Title",
                        "url": "https://example.com/ds",
                        "summary": "DS Summary",
                        "rerank_score": 2.8,
                    }
                ]
            },
        )
        FakeHybridClient.get_response = FakeResponse(403, text="Forbidden")

        with patch.object(search_tool.httpx, "AsyncClient", FakeHybridClient):
            output = await search_tool.execute(_cfg(), {"query": "test query"})

        payload = json.loads(output)
        self.assertEqual(len(payload["results"]), 1)
        self.assertEqual(payload["results"][0]["source"], "deepseek")
        self.assertEqual(payload["sources"]["deepseek"], 1)
        self.assertEqual(payload["sources"]["searxng"], 0)
        self.assertIn("errors", payload)
        self.assertIn("403", payload["errors"]["searxng"])

    async def test_no_api_key_gracefully_runs_searxng_only(self) -> None:
        FakeHybridClient.get_response = FakeResponse(
            200,
            {
                "results": [
                    {
                        "title": "SX Title",
                        "url": "https://example.com/sx",
                        "content": "SX Content",
                    }
                ]
            },
        )

        with patch.object(search_tool.httpx, "AsyncClient", FakeHybridClient):
            output = await search_tool.execute(_cfg(api_key=""), {"query": "test query"})

        # 没有给 DeepSeek 发请求
        self.assertEqual(len(FakeHybridClient.posts), 0)
        self.assertEqual(len(FakeHybridClient.gets), 1)

        payload = json.loads(output)
        self.assertEqual(len(payload["results"]), 1)
        self.assertEqual(payload["results"][0]["source"], "searxng")
        self.assertEqual(payload["sources"]["deepseek"], 0)
        self.assertEqual(payload["sources"]["searxng"], 1)
        self.assertNotIn("errors", payload)

    async def test_deepseek_fails_searxng_succeeds(self) -> None:
        FakeHybridClient.post_response = FakeResponse(500, text="Internal Server Error")
        FakeHybridClient.get_response = FakeResponse(
            200,
            {
                "results": [
                    {
                        "title": "SX Title",
                        "url": "https://example.com/sx",
                        "content": "SX Content",
                    }
                ]
            },
        )

        with patch.object(search_tool.httpx, "AsyncClient", FakeHybridClient):
            output = await search_tool.execute(_cfg(), {"query": "test query"})

        payload = json.loads(output)
        self.assertEqual(len(payload["results"]), 1)
        self.assertEqual(payload["results"][0]["source"], "searxng")
        self.assertIn("errors", payload)
        self.assertIn("500", payload["errors"]["deepseek"])

    async def test_both_fail_returns_error(self) -> None:
        FakeHybridClient.post_response = FakeResponse(500, text="DeepSeek Down")
        FakeHybridClient.get_response = FakeResponse(502, text="SearXNG Down")

        with patch.object(search_tool.httpx, "AsyncClient", FakeHybridClient):
            output = await search_tool.execute(_cfg(), {"query": "test query"})

        payload = json.loads(output)
        self.assertIn("error", payload)
        self.assertIn("errors", payload)
        self.assertIn("deepseek", payload["errors"])
        self.assertIn("searxng", payload["errors"])


if __name__ == "__main__":
    unittest.main()
