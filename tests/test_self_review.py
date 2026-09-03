from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.exception import MockApiException

from plugins.aiagent.review import ReviewResult
from plugins.self_review import (
    _bypass,
    _cache,
    _dimension_enabled,
    _target_under_review,
    review_outgoing_message,
)
from plugins.self_review.config import DEFAULT_SELF_REVIEW_PROMPT, get_config
from plugins.self_review.extract import api_kind, extract_text, payload_of


def permissions(**kwargs: Any) -> dict[str, Any]:
    whitelist = kwargs.get("whitelist") or {}
    blacklist = kwargs.get("blacklist") or {}
    return {
        "permissions": {
            "whitelist": {"enable": False, "user": [], "group": [], **whitelist},
            "blacklist": {"enable": False, "user": [], "group": [], **blacklist},
        }
    }


class ApiKindTests(unittest.TestCase):
    def test_plain_send_apis(self) -> None:
        for api in ("send_msg", "send_group_msg", "send_private_msg"):
            self.assertEqual(api_kind(api), "text")

    def test_forward_apis(self) -> None:
        for api in ("send_forward_msg", "send_group_forward_msg", "send_private_forward_msg"):
            self.assertEqual(api_kind(api), "forward")

    def test_unrelated_api(self) -> None:
        self.assertEqual(api_kind("delete_msg"), "")

    def test_payload_key_depends_on_kind(self) -> None:
        data = {"message": "a", "messages": ["b"]}
        self.assertEqual(payload_of(data, "text"), "a")
        self.assertEqual(payload_of(data, "forward"), ["b"])


class ExtractTextTests(unittest.TestCase):
    def test_plain_string(self) -> None:
        self.assertEqual(extract_text("  你好  "), "你好")

    def test_cq_codes_are_stripped(self) -> None:
        self.assertEqual(extract_text("标题：晴天[CQ:image,file=a.jpg]"), "标题：晴天")

    def test_cq_escapes_are_restored(self) -> None:
        self.assertEqual(extract_text("&#91;测试&#93;"), "[测试]")

    def test_message_object_keeps_only_text(self) -> None:
        message = Message(
            [MessageSegment.text("作者：某人"), MessageSegment.image("file:///a.jpg"), MessageSegment.text("简介：无")]
        )
        self.assertEqual(extract_text(message), "作者：某人\n简介：无")

    def test_dict_segments(self) -> None:
        payload = [
            {"type": "text", "data": {"text": "第一段"}},
            {"type": "at", "data": {"qq": "1"}},
            {"type": "text", "data": {"text": "第二段"}},
        ]
        self.assertEqual(extract_text(payload), "第一段\n第二段")

    def test_forward_nodes_are_flattened(self) -> None:
        nodes = [
            MessageSegment.node_custom(1, "bot", Message("标题：某视频")),
            MessageSegment.node_custom(1, "bot", Message([MessageSegment.text("热评：好看")])),
        ]
        self.assertEqual(extract_text(nodes), "标题：某视频\n热评：好看")

    def test_dict_node_with_string_content(self) -> None:
        nodes = [{"type": "node", "data": {"content": "简介：一段说明"}}]
        self.assertEqual(extract_text(nodes), "简介：一段说明")

    def test_media_only_payload_is_empty(self) -> None:
        self.assertEqual(extract_text(Message(MessageSegment.record("file:///a.mp3"))), "")

    def test_none_payload(self) -> None:
        self.assertEqual(extract_text(None), "")


class TargetScopeTests(unittest.TestCase):
    def test_no_rules_reviews_everything(self) -> None:
        self.assertTrue(_target_under_review(permissions(), "111", ""))
        self.assertTrue(_target_under_review(permissions(), "", "222"))

    def test_group_whitelist_limits_review(self) -> None:
        cfg = permissions(whitelist={"enable": True, "group_enable": True, "group": ["111"]})
        self.assertTrue(_target_under_review(cfg, "111", ""))
        self.assertFalse(_target_under_review(cfg, "999", ""))

    def test_group_blacklist_skips_review(self) -> None:
        cfg = permissions(blacklist={"enable": True, "group_enable": True, "group": ["111"]})
        self.assertFalse(_target_under_review(cfg, "111", ""))
        self.assertTrue(_target_under_review(cfg, "999", ""))

    def test_group_whitelist_does_not_block_private(self) -> None:
        cfg = permissions(
            whitelist={"enable": True, "group_enable": True, "user_enable": False, "group": ["111"]}
        )
        self.assertTrue(_target_under_review(cfg, "", "222"))

    def test_dimension_flag_overrides_enable(self) -> None:
        self.assertFalse(_dimension_enabled({"enable": True, "group_enable": False}, "group"))
        self.assertTrue(_dimension_enabled({"enable": True}, "group"))


class HookTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _cache.clear()

    @staticmethod
    def _cfg(**overrides: Any) -> dict[str, Any]:
        cfg = {
            "enabled": True,
            "review": {
                "enabled": True,
                "min_chars": 4,
                "max_chars": 1500,
                "temperature": 0.0,
                "max_tokens": 300,
                "timeout_seconds": 12,
                "max_concurrent": 2,
                "cache_size": 8,
                "prompt": "p",
            },
            "scope": {"group": True, "private": True, "forward": True},
            "action": {"block": True, "notify_chat": False, "notify_superuser": False},
            "permissions": {},
        }
        for section, values in overrides.items():
            cfg[section].update(values) if isinstance(cfg[section], dict) else None
        return cfg

    async def _call(self, cfg: dict[str, Any], verdict: Any, api: str = "send_group_msg", **data: Any) -> Any:
        with (
            patch("plugins.self_review.get_config", return_value=cfg),
            patch("plugins.self_review.request_verdict", verdict) as mocked,
        ):
            payload = {"group_id": 111, "message": Message("这是一段需要审查的文本")}
            payload.update(data)
            await review_outgoing_message(object(), api, payload)
        return mocked

    async def test_risk_blocks_the_send(self) -> None:
        verdict = AsyncMock(return_value=ReviewResult(risk=True, reason="测试"))
        with self.assertRaises(MockApiException) as caught:
            await self._call(self._cfg(), verdict)
        self.assertIsNone(caught.exception.result)

    async def test_clean_text_passes_through(self) -> None:
        verdict = AsyncMock(return_value=ReviewResult(risk=False, reason=""))
        await self._call(self._cfg(), verdict)

    async def test_block_disabled_only_logs(self) -> None:
        verdict = AsyncMock(return_value=ReviewResult(risk=True, reason="测试"))
        await self._call(self._cfg(action={"block": False}), verdict)

    async def test_unrelated_api_is_ignored(self) -> None:
        verdict = AsyncMock(return_value=ReviewResult(risk=True, reason="测试"))
        mocked = await self._call(self._cfg(), verdict, api="delete_msg")
        mocked.assert_not_awaited()

    async def test_short_text_is_not_reviewed(self) -> None:
        verdict = AsyncMock(return_value=ReviewResult(risk=True, reason="测试"))
        mocked = await self._call(self._cfg(), verdict, message=Message("嗯"))
        mocked.assert_not_awaited()

    async def test_media_only_send_is_not_reviewed(self) -> None:
        verdict = AsyncMock(return_value=ReviewResult(risk=True, reason="测试"))
        mocked = await self._call(self._cfg(), verdict, message=Message(MessageSegment.image("file:///a.jpg")))
        mocked.assert_not_awaited()

    async def test_group_scope_off_skips_group_send(self) -> None:
        verdict = AsyncMock(return_value=ReviewResult(risk=True, reason="测试"))
        mocked = await self._call(self._cfg(scope={"group": False}), verdict)
        mocked.assert_not_awaited()

    async def test_timeout_fails_open(self) -> None:
        verdict = AsyncMock(side_effect=asyncio.TimeoutError())
        await self._call(self._cfg(), verdict)

    async def test_verdict_error_fails_open(self) -> None:
        verdict = AsyncMock(side_effect=RuntimeError("boom"))
        await self._call(self._cfg(), verdict)

    async def test_identical_text_is_only_reviewed_once(self) -> None:
        verdict = AsyncMock(return_value=ReviewResult(risk=False, reason=""))
        cfg = self._cfg()
        await self._call(cfg, verdict)
        await self._call(cfg, verdict)
        self.assertEqual(verdict.await_count, 1)

    async def test_bypass_flag_skips_review(self) -> None:
        verdict = AsyncMock(return_value=ReviewResult(risk=True, reason="测试"))
        token = _bypass.set(True)
        try:
            mocked = await self._call(self._cfg(), verdict)
        finally:
            _bypass.reset(token)
        mocked.assert_not_awaited()


class ConfigTests(unittest.TestCase):
    def test_blank_prompt_falls_back_to_default(self) -> None:
        with patch("plugins.self_review.config.load_plugin_config", return_value={"review": {"prompt": " "}}):
            self.assertEqual(get_config()["review"]["prompt"], DEFAULT_SELF_REVIEW_PROMPT)

    def test_numeric_fields_are_clamped(self) -> None:
        raw = {"review": {"timeout_seconds": 999, "max_concurrent": 0, "cache_size": -5}}
        with patch("plugins.self_review.config.load_plugin_config", return_value=raw):
            review = get_config()["review"]
        self.assertEqual(review["timeout_seconds"], 60)
        self.assertEqual(review["max_concurrent"], 1)
        self.assertEqual(review["cache_size"], 0)

    def test_defaults_keep_review_disabled(self) -> None:
        with patch("plugins.self_review.config.load_plugin_config", return_value={}):
            cfg = get_config()
        self.assertFalse(cfg["enabled"])
        self.assertFalse(cfg["review"]["enabled"])
        self.assertTrue(cfg["scope"]["forward"])
        self.assertTrue(cfg["action"]["block"])
        self.assertFalse(cfg["action"]["notify_chat"])


if __name__ == "__main__":
    unittest.main()
