"""AI Agent 群聊自动回复：白名单、同群冷却与静默失败。"""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import nonebot
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message

import plugins.aiagent as aiagent
from plugins.aiagent import auto_reply
from plugins.aiagent import config as aiagent_config
from plugins.aiagent import quota as quota_mod

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="nonebot.drivers.none:Driver")

from plugins.bot_admin import settings as admin_settings

GROUP_ID = 10001
USER_ID = 20001
SELF_ID = 99999


def make_group_event(
    *,
    group_id: int = GROUP_ID,
    user_id: int = USER_ID,
    self_id: int = SELF_ID,
    text: str = "你好",
    to_me: bool = False,
) -> GroupMessageEvent:
    return GroupMessageEvent.model_validate(
        {
            "time": 1700000000,
            "self_id": self_id,
            "post_type": "message",
            "message_type": "group",
            "sub_type": "normal",
            "message_id": 1,
            "user_id": user_id,
            "group_id": group_id,
            "raw_message": text,
            "font": 0,
            "to_me": to_me,
            "sender": {"user_id": user_id, "nickname": "tester", "role": "member"},
            "message": [{"type": "text", "data": {"text": text}}],
        }
    )


class RecordingBot:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.messages: list[Message] = []

    async def send(self, event: Any, message: Message) -> None:
        self.messages.append(message)
        # 引用段不算文本内容，这里只记录真正发出的文字
        self.sent.append(message.extract_plain_text())


def enabled_cfg(**overrides: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "enabled": True,
        "chat": {"max_history_messages": 10, "cooldown_seconds": 0, "max_reply_chars": 3500},
        "memory": {"enabled": False},
        "tools": {"files": {"enabled": False}, "plugin_tools": {"enabled": False}},
        "quota": {"enabled": False},
        "permissions": {},
        "vision": {"enabled": False},
        "auto_reply": {"enabled": False, "groups": [], "cooldown_seconds": 5},
    }
    cfg.update(overrides)
    return cfg


class AutoReplyConfigTests(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        cfg = {"auto_reply": {"enabled": False, "groups": [str(GROUP_ID)]}}
        self.assertFalse(auto_reply.allowed(cfg, GROUP_ID))
        self.assertEqual(auto_reply.cooldown_seconds({}), 5)

    def test_whitelist_requires_explicit_group(self) -> None:
        cfg = {"auto_reply": {"enabled": True, "groups": []}}
        self.assertFalse(auto_reply.allowed(cfg, GROUP_ID))

        cfg["auto_reply"]["groups"] = [str(GROUP_ID)]
        self.assertTrue(auto_reply.allowed(cfg, GROUP_ID))
        self.assertFalse(auto_reply.allowed(cfg, 10002))

    def test_whitelist_has_no_count_limit(self) -> None:
        groups = [str(100000 + index) for index in range(5000)]
        cfg = {"auto_reply": {"enabled": True, "groups": groups}}
        self.assertEqual(len(auto_reply.groups(cfg)), 5000)
        self.assertTrue(auto_reply.allowed(cfg, groups[-1]))

    def test_groups_accepts_comma_string_and_ignores_invalid(self) -> None:
        cfg = {"auto_reply": {"enabled": True, "groups": "123, 456，abc, 789"}}
        self.assertEqual(auto_reply.groups(cfg), {"123", "456", "789"})

    def test_normalize_adds_global_defaults(self) -> None:
        doc = aiagent_config._normalize_document({"enabled": True})
        self.assertEqual(
            doc["auto_reply"],
            {
                "enabled": False,
                "groups": [],
                "cooldown_seconds": 5,
                "context_messages": 10,
                "context_max_chars": 1200,
            },
        )

    def test_normalize_preserves_overrides(self) -> None:
        doc = aiagent_config._normalize_document(
            {
                "auto_reply": {
                    "enabled": True,
                    "groups": ["123"],
                    "cooldown_seconds": 9,
                    "context_messages": 4,
                    "context_max_chars": 800,
                }
            }
        )
        self.assertEqual(
            doc["auto_reply"],
            {
                "enabled": True,
                "groups": ["123"],
                "cooldown_seconds": 9,
                "context_messages": 4,
                "context_max_chars": 800,
            },
        )

    def test_effective_config_keeps_global_auto_reply(self) -> None:
        doc = aiagent_config._normalize_document(
            {"auto_reply": {"enabled": True, "groups": ["123"], "cooldown_seconds": 7}}
        )
        cfg = aiagent_config._effective_config(doc)
        self.assertEqual(
            cfg["auto_reply"],
            {
                "enabled": True,
                "groups": ["123"],
                "cooldown_seconds": 7,
                "context_messages": 10,
                "context_max_chars": 1200,
            },
        )


class AutoReplyAdminValidationTests(unittest.TestCase):
    def _save(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = copy.deepcopy(aiagent_config.DEFAULT_CONFIG)
        with (
            patch.object(admin_settings, "get_aiagent_config", Mock(return_value=current)),
            patch.object(
                admin_settings,
                "resolve_aiagent_persona_path",
                Mock(return_value=Path("BotData/agent_personas/default")),
            ),
            patch.object(admin_settings, "save_aiagent_config", Mock(side_effect=lambda data: data)),
        ):
            return admin_settings._update_aiagent_config(payload)

    def test_enabling_requires_at_least_one_group(self) -> None:
        with self.assertRaises(ValueError):
            self._save({"auto_reply": {"enabled": True, "groups": []}})

    def test_whitelist_and_cooldown_round_trip(self) -> None:
        result = self._save(
            {
                "auto_reply": {
                    "enabled": True,
                    "groups": ["123456"],
                    "cooldown_seconds": 9,
                    "context_messages": 4,
                    "context_max_chars": 600,
                }
            }
        )
        self.assertEqual(
            result["auto_reply"],
            {
                "enabled": True,
                "groups": ["123456"],
                "cooldown_seconds": 9,
                "context_messages": 4,
                "context_max_chars": 600,
            },
        )

    def test_multiline_whitelist_is_deduped_and_filtered(self) -> None:
        result = self._save(
            {
                "auto_reply": {
                    "enabled": True,
                    "groups": "123456\n789012，345678, 123456\nnot-a-group",
                }
            }
        )
        self.assertEqual(result["auto_reply"]["groups"], ["123456", "789012", "345678"])

    def test_keeps_existing_whitelist_when_form_omits_it(self) -> None:
        current = copy.deepcopy(aiagent_config.DEFAULT_CONFIG)
        current["auto_reply"] = {"enabled": True, "groups": ["123456"], "cooldown_seconds": 5}
        with (
            patch.object(admin_settings, "get_aiagent_config", Mock(return_value=current)),
            patch.object(
                admin_settings,
                "resolve_aiagent_persona_path",
                Mock(return_value=Path("BotData/agent_personas/default")),
            ),
            patch.object(admin_settings, "save_aiagent_config", Mock(side_effect=lambda data: data)),
        ):
            result = admin_settings._update_aiagent_config({})
        self.assertEqual(result["auto_reply"]["groups"], ["123456"])
        self.assertTrue(result["auto_reply"]["enabled"])


class AutoReplyCooldownTests(unittest.TestCase):
    def setUp(self) -> None:
        auto_reply.reset_state()

    def tearDown(self) -> None:
        auto_reply.reset_state()

    def _cfg(self, cooldown: int = 5) -> dict[str, Any]:
        return {
            "auto_reply": {
                "enabled": True,
                "groups": [str(GROUP_ID)],
                "cooldown_seconds": cooldown,
            }
        }

    def test_claim_blocks_until_cooldown_passes(self) -> None:
        cfg = self._cfg()
        self.assertTrue(auto_reply.claim(cfg, GROUP_ID, now=100.0))
        auto_reply.release(GROUP_ID)

        self.assertFalse(auto_reply.claim(cfg, GROUP_ID, now=104.9))
        self.assertTrue(auto_reply.claim(cfg, GROUP_ID, now=105.0))

    def test_claim_blocks_while_previous_reply_is_inflight(self) -> None:
        cfg = self._cfg(cooldown=0)
        self.assertTrue(auto_reply.claim(cfg, GROUP_ID, now=100.0))
        self.assertFalse(auto_reply.claim(cfg, GROUP_ID, now=101.0))

        auto_reply.release(GROUP_ID)
        self.assertTrue(auto_reply.claim(cfg, GROUP_ID, now=102.0))

    def test_note_bot_spoke_starts_cooldown(self) -> None:
        cfg = self._cfg()
        auto_reply.note_bot_spoke(cfg, GROUP_ID, now=200.0)
        self.assertFalse(auto_reply.claim(cfg, GROUP_ID, now=204.0))
        self.assertTrue(auto_reply.claim(cfg, GROUP_ID, now=205.0))

    def test_note_bot_spoke_ignores_non_whitelisted_group(self) -> None:
        cfg = self._cfg()
        auto_reply.note_bot_spoke(cfg, 88888, now=300.0)
        self.assertFalse(auto_reply.claim(cfg, 88888, now=301.0))


class AutoReplyMatchingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        auto_reply.reset_state()

    def tearDown(self) -> None:
        auto_reply.reset_state()

    async def _run_matcher(
        self,
        cfg: dict[str, Any],
        event: GroupMessageEvent,
        *,
        handled: bool = False,
    ) -> tuple[bool, AsyncMock]:
        handler = AsyncMock()
        with (
            patch.object(aiagent, "is_command_handled", return_value=handled),
            patch.object(aiagent, "get_config_for_event", return_value=cfg),
            patch.object(aiagent, "is_event_allowed", return_value=True),
            patch.object(aiagent, "_handle_chat_event", handler),
        ):
            await aiagent._handle_auto_chat(RecordingBot(), event)
        return handler.await_count > 0, handler

    async def test_non_whitelisted_group_is_ignored(self) -> None:
        cfg = enabled_cfg(
            auto_reply={"enabled": True, "groups": ["99999"], "cooldown_seconds": 5}
        )
        called, _ = await self._run_matcher(cfg, make_group_event())
        self.assertFalse(called)

    async def test_whitelisted_group_calls_ai_in_auto_mode(self) -> None:
        cfg = enabled_cfg(
            auto_reply={"enabled": True, "groups": [str(GROUP_ID)], "cooldown_seconds": 5}
        )
        called, handler = await self._run_matcher(cfg, make_group_event())
        self.assertTrue(called)
        self.assertTrue(handler.await_args.kwargs["auto"])

    async def test_direct_at_still_uses_normal_mode(self) -> None:
        cfg = enabled_cfg(auto_reply={"enabled": False, "groups": []})
        called, handler = await self._run_matcher(cfg, make_group_event(to_me=True))
        self.assertTrue(called)
        self.assertFalse(handler.await_args.kwargs["auto"])

    async def test_empty_message_does_not_consume_cooldown(self) -> None:
        cfg = enabled_cfg(
            auto_reply={"enabled": True, "groups": [str(GROUP_ID)], "cooldown_seconds": 5}
        )
        called_empty, _ = await self._run_matcher(cfg, make_group_event(text=""))
        called_text, _ = await self._run_matcher(cfg, make_group_event(text="你好"))
        self.assertFalse(called_empty)
        self.assertTrue(called_text)

    async def test_handled_message_is_ignored(self) -> None:
        cfg = enabled_cfg(
            auto_reply={"enabled": True, "groups": [str(GROUP_ID)], "cooldown_seconds": 5}
        )
        called, _ = await self._run_matcher(cfg, make_group_event(), handled=True)
        self.assertFalse(called)

    async def test_bot_own_message_is_ignored(self) -> None:
        cfg = enabled_cfg(
            auto_reply={"enabled": True, "groups": [str(GROUP_ID)], "cooldown_seconds": 5}
        )
        called, _ = await self._run_matcher(
            cfg, make_group_event(user_id=SELF_ID, self_id=SELF_ID)
        )
        self.assertFalse(called)


class AutoReplyBehaviorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_quota_path = quota_mod.QUOTA_PATH
        quota_mod.QUOTA_PATH = Path(self._tmpdir.name) / "aiagent_quota.json"
        quota_mod.reset_usage_state()
        aiagent._last_used_at.clear()

    def tearDown(self) -> None:
        quota_mod.QUOTA_PATH = self._orig_quota_path
        quota_mod.reset_usage_state()
        aiagent._last_used_at.clear()
        self._tmpdir.cleanup()

    async def _run(
        self,
        cfg: dict[str, Any],
        *,
        auto: bool,
        reply: Any = "你好呀",
        text: str = "你好",
    ) -> tuple[list[str], bool]:
        bot = RecordingBot()
        event = make_group_event(text=text)
        called = False

        async def fake_reply(*args: Any, **kwargs: Any) -> str:
            nonlocal called
            called = True
            if isinstance(reply, Exception):
                raise reply
            return reply

        with (
            patch.object(aiagent, "get_config_for_event", return_value=cfg),
            patch.object(aiagent, "is_event_allowed", return_value=True),
            patch.object(aiagent, "request_chat_completion", fake_reply),
            patch.object(aiagent, "read_recent_messages", return_value=[]),
            patch.object(aiagent, "stats_increment", lambda *args, **kwargs: None),
        ):
            await aiagent._handle_chat_event(bot, event, text, auto=auto)
        self.last_bot = bot
        return bot.sent, called

    async def test_auto_reply_ignores_reset_phrase(self) -> None:
        sent, called = await self._run(enabled_cfg(), auto=True, text="重置")
        self.assertFalse(called)
        self.assertEqual(sent, [])

    async def test_auto_reply_bypasses_user_cooldown(self) -> None:
        cfg = enabled_cfg(chat={"max_history_messages": 10, "cooldown_seconds": 3600, "max_reply_chars": 3500})
        aiagent._last_used_at[str(USER_ID)] = 1e9
        sent, called = await self._run(cfg, auto=True)
        self.assertTrue(called)
        self.assertEqual(sent, ["你好呀"])

    async def test_auto_reply_does_not_quote_trigger_message(self) -> None:
        sent, called = await self._run(enabled_cfg(), auto=True)
        self.assertTrue(called)
        self.assertEqual(sent, ["你好呀"])
        # 主动接话不能看起来像在针对某条消息回答
        self.assertNotEqual(self.last_bot.messages[0][0].type, "reply")

    async def test_auto_reply_can_skip_with_no_reply_sentinel(self) -> None:
        sent, called = await self._run(enabled_cfg(), auto=True, reply="<NO_REPLY>")
        self.assertTrue(called)
        self.assertEqual(sent, [])

    def test_auto_prompt_includes_group_context_and_skip_rule(self) -> None:
        messages = aiagent._build_messages(
            enabled_cfg(),
            make_group_event(),
            "group:10001:user:20001",
            "【当前时间】2026-10-24 23:00\n你好",
            auto=True,
            auto_context="【本群最近聊天】\n[23:00] 群友: 刚刚那条不是对你说的",
        )
        system_text = "\n".join(
            str(message.get("content") or "")
            for message in messages
            if message.get("role") == "system"
        )
        self.assertIn("主动接话模式", system_text)
        self.assertIn("<NO_REPLY>", system_text)
        self.assertIn("不是对你说的", system_text)

    async def test_quote_reply_can_be_disabled(self) -> None:
        cfg = enabled_cfg(
            chat={
                "max_history_messages": 10,
                "cooldown_seconds": 0,
                "max_reply_chars": 3500,
                "quote_reply": False,
            }
        )
        sent, called = await self._run(cfg, auto=True)
        self.assertTrue(called)
        self.assertEqual(sent, ["你好呀"])
        self.assertEqual([segment.type for segment in self.last_bot.messages[0]], ["text"])

    async def test_auto_reply_does_not_spend_quota(self) -> None:
        cfg = enabled_cfg(
            quota={
                "enabled": True,
                "default_group": {"daily": 1, "hourly": 10},
                "default_user": {"daily": 1, "hourly": 10},
                "exempt_user_ids": [],
                "exempt_group_ids": [],
            }
        )
        event = make_group_event()
        quota_mod.record_usage(cfg, event, 1)

        sent, called = await self._run(cfg, auto=True)
        self.assertTrue(called)
        self.assertEqual(sent, ["你好呀"])
        self.assertEqual(quota_mod.get_quota_status(cfg, event)["daily"]["used"], 1)

    async def test_auto_reply_failure_is_silent(self) -> None:
        sent, called = await self._run(enabled_cfg(), auto=True, reply=RuntimeError("boom"))
        self.assertTrue(called)
        self.assertEqual(sent, [])

    async def test_direct_reply_failure_still_notifies(self) -> None:
        sent, called = await self._run(enabled_cfg(), auto=False, reply=RuntimeError("boom"))
        self.assertTrue(called)
        self.assertEqual(len(sent), 1)


class QuoteReplyTests(unittest.TestCase):
    """AI 回复默认引用触发它的消息，可配置关闭。"""

    def test_reply_quotes_trigger_message_by_default(self) -> None:
        event = make_group_event()
        message = aiagent._build_reply_message(event, "你好呀", {})

        self.assertEqual(message[0].type, "reply")
        self.assertEqual(str(message[0].data["id"]), "1")
        self.assertEqual(message.extract_plain_text(), "你好呀")

    def test_quote_can_be_disabled(self) -> None:
        event = make_group_event()
        message = aiagent._build_reply_message(event, "你好呀", {"chat": {"quote_reply": False}})

        self.assertEqual([segment.type for segment in message], ["text"])
        self.assertEqual(message.extract_plain_text(), "你好呀")

    def test_missing_message_id_falls_back_to_plain_text(self) -> None:
        message = aiagent._build_reply_message(SimpleNamespace(), "你好呀", {})

        self.assertEqual([segment.type for segment in message], ["text"])
        self.assertEqual(message.extract_plain_text(), "你好呀")


if __name__ == "__main__":
    unittest.main()
