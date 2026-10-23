"""AI Agent 用量日志：缓存命中字段解析。

Chat Completions 用 prompt_cache_hit_tokens / prompt_cache_miss_tokens；
Responses API 用 input_tokens_details.cached_tokens。
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from plugins.aiagent import client as aiagent_client
from plugins.aiagent import responses_client
from plugins.aiagent import usage as usage_mod


class _FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def json(self) -> dict[str, Any]:
        return self._data


class _FakeAsyncClient:
    response_data: dict[str, Any] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]):
        return _FakeResponse(_FakeAsyncClient.response_data)


def _model_cfg() -> dict[str, Any]:
    return {
        "base_url": "https://api.deepseek.com",
        "api_key": "test-key",
        "model": "deepseek-flash",
        "temperature": 0.7,
        "top_p": 1.0,
        "max_tokens": 256,
        "timeout_seconds": 5,
        "proxy": "",
    }


class UsageLogTests(unittest.TestCase):
    def test_chat_usage_reports_hit_and_miss(self) -> None:
        with self.assertLogs("HikariBot.AIAgent.Usage", level="INFO") as captured:
            usage_mod.log_chat_usage(
                "chat",
                "deepseek-flash",
                {
                    "prompt_tokens": 1000,
                    "prompt_cache_hit_tokens": 900,
                    "prompt_cache_miss_tokens": 100,
                    "completion_tokens": 30,
                },
            )

        line = "\n".join(captured.output)
        self.assertIn("caller=chat", line)
        self.assertIn("input=1000", line)
        self.assertIn("hit=900", line)
        self.assertIn("miss=100", line)
        self.assertIn("hit_rate=90.0%", line)
        self.assertIn("output=30", line)

    def test_responses_usage_reads_cached_tokens(self) -> None:
        with self.assertLogs("HikariBot.AIAgent.Usage", level="INFO") as captured:
            usage_mod.log_responses_usage(
                "review",
                "deepseek-flash",
                {
                    "input_tokens": 1000,
                    "input_tokens_details": {"cached_tokens": 800},
                    "output_tokens": 20,
                },
            )

        line = "\n".join(captured.output)
        self.assertIn("caller=review", line)
        self.assertIn("hit=800", line)
        self.assertIn("miss=200", line)
        self.assertIn("hit_rate=80.0%", line)

    def test_missing_usage_does_not_log(self) -> None:
        with self.assertNoLogs("HikariBot.AIAgent.Usage", level="INFO"):
            usage_mod.log_chat_usage("chat", "deepseek-flash", None)
            usage_mod.log_chat_usage("chat", "deepseek-flash", {})
            usage_mod.log_responses_usage("chat", "deepseek-flash", {})


class UsageIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_responses_client_logs_usage(self) -> None:
        _FakeAsyncClient.response_data = {
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "ok"}],
                }
            ],
            "output_text": "ok",
            "usage": {
                "input_tokens": 1000,
                "input_tokens_details": {"cached_tokens": 800},
                "output_tokens": 10,
            },
        }
        cfg = {"model": _model_cfg(), "thinking": {"enabled": False}}

        with (
            patch.object(responses_client.httpx, "AsyncClient", _FakeAsyncClient),
            self.assertLogs("HikariBot.AIAgent.Usage", level="INFO") as captured,
        ):
            data = await responses_client.post_response(
                cfg, "sys", [{"role": "user", "content": "hi"}], []
            )

        self.assertEqual(data["output_text"], "ok")
        line = "\n".join(captured.output)
        self.assertIn("caller=chat", line)
        self.assertIn("hit=800", line)
        self.assertIn("hit_rate=80.0%", line)

    async def test_chat_client_logs_usage(self) -> None:
        _FakeAsyncClient.response_data = {
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {
                "prompt_tokens": 100,
                "prompt_cache_hit_tokens": 90,
                "prompt_cache_miss_tokens": 10,
                "completion_tokens": 5,
            },
        }
        cfg = {"model": _model_cfg(), "thinking": {"enabled": False}}

        with (
            patch.object(aiagent_client.httpx, "AsyncClient", _FakeAsyncClient),
            self.assertLogs("HikariBot.AIAgent.Usage", level="INFO") as captured,
        ):
            message = await aiagent_client.post_chat_completion(
                cfg, [{"role": "user", "content": "hi"}], []
            )

        self.assertEqual(message["content"], "ok")
        line = "\n".join(captured.output)
        self.assertIn("caller=chat", line)
        self.assertIn("hit=90", line)
        self.assertIn("hit_rate=90.0%", line)
