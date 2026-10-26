"""AI Agent 群管理 / 互动工具（禁言、踢人、戳一戳、@）测试。"""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import patch

from nonebot.adapters.onebot.v11 import GroupMessageEvent

from core.ai_tool_registry import AIToolContext
from plugins.aiagent.tools import group as group_tool
from plugins.aiagent.tools import group_actions
from plugins.aiagent.tools import registry as tool_registry

GROUP_ID = 10001
BOT_ID = 99999
USER_ID = 20001  # 普通群员 / 默认发起人
ADMIN_ID = 20002  # 管理员
OTHER_ID = 20003
OWNER_ID = 30003

EVENT_TIME = 1700000000


def _member(
    user_id: int, *, nickname: str = "", role: str = "member", **extra: Any
) -> dict[str, Any]:
    member: dict[str, Any] = {
        "user_id": user_id,
        "nickname": nickname or f"nick{user_id}",
        "card": "",
        "role": role,
        "level": "3",
        "join_time": 1600000000,
        "last_sent_time": 1700000000,
        "title": "",
    }
    member.update(extra)
    return member


MEMBERS = [
    _member(USER_ID, nickname="小明"),
    _member(ADMIN_ID, nickname="小红", role="admin"),
    _member(OTHER_ID, nickname="小刚"),
    _member(OWNER_ID, nickname="群主", role="owner"),
]


def _group_event(
    user_id: int = USER_ID,
    *,
    role: str = "member",
    group_id: int = GROUP_ID,
    text: str = "hi",
) -> GroupMessageEvent:
    return GroupMessageEvent(
        time=EVENT_TIME,
        self_id=BOT_ID,
        post_type="message",
        message_type="group",
        sub_type="normal",
        message_id=1,
        group_id=group_id,
        user_id=user_id,
        raw_message=text,
        font=0,
        sender={
            "user_id": user_id,
            "nickname": f"nick{user_id}",
            "card": "",
            "role": role,
        },
        message=[{"type": "text", "data": {"text": text}}],
    )


class FakeBot:
    def __init__(
        self,
        *,
        bot_role: str = "admin",
        members: list[dict[str, Any]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.self_id = str(BOT_ID)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._bot_role = bot_role
        self._members = MEMBERS if members is None else members
        self._error = error
        self._fail_apis: set[str] = set()

    def fail_api(self, api: str) -> None:
        self._fail_apis.add(api)

    async def call_api(self, api: str, **kwargs: Any) -> Any:
        self.calls.append((api, kwargs))
        if self._error is not None:
            raise self._error
        if api in self._fail_apis:
            raise RuntimeError(f"{api} boom")
        if api == "get_group_member_list":
            return self._members
        if api == "get_group_member_info":
            return self._member_info(**kwargs)
        return None

    def _member_info(self, **kwargs: Any) -> dict[str, Any]:
        user_id = int(kwargs.get("user_id") or 0)
        if user_id == BOT_ID:
            return _member(BOT_ID, nickname="机器人", role=self._bot_role)
        for member in self._members:
            if int(member["user_id"]) == user_id:
                return dict(member)
        return _member(user_id)

    def api_names(self) -> list[str]:
        return [call[0] for call in self.calls]


def _cfg(**overrides: Any) -> dict[str, Any]:
    tools: dict[str, Any] = {
        "group_members": {"enabled": True, "max_members": 100},
        "member_profile": {"enabled": True},
        "user_messages": {"enabled": True},
        "group_mute": {
            "enabled": True,
            "require_requester_admin": True,
            "default_minutes": 10,
            "max_minutes": 43200,
        },
        "group_kick": {"enabled": True, "require_requester_admin": True},
        "group_poke": {"enabled": True, "max_per_hour": 20},
        "group_mention": {"enabled": True, "max_per_reply": 3},
    }
    tools.update(overrides.pop("tools", {}))
    cfg: dict[str, Any] = {"tools": tools}
    cfg.update(overrides)
    return cfg


def _context(bot: Any, event: Any, *, auto: bool = False) -> AIToolContext:
    return AIToolContext(bot=bot, event=event, auto=auto)


async def _run(
    name: str,
    arguments: dict[str, Any],
    *,
    bot: Any = None,
    cfg: dict[str, Any] | None = None,
    event: Any = None,
    auto: bool = False,
) -> dict[str, Any]:
    bot = bot if bot is not None else FakeBot()
    event = event if event is not None else _group_event()
    content = await group_actions.execute(
        name, cfg or _cfg(), arguments, _context(bot, event, auto=auto)
    )
    return json.loads(content)


class _ResetStateMixin(unittest.TestCase):
    def setUp(self) -> None:
        group_actions._bot_role_cache.clear()
        group_actions._poke_times.clear()


class DefinitionTests(_ResetStateMixin):
    def test_definitions_present_for_group_chat(self) -> None:
        names = {
            tool["function"]["name"]
            for tool in group_actions.definitions(
                _cfg(), _context(FakeBot(), _group_event())
            )
        }
        self.assertEqual(
            names, {"group_mute", "group_kick", "group_poke", "group_mention"}
        )

    def test_definitions_absent_in_auto_reply(self) -> None:
        self.assertEqual(
            group_actions.definitions(
                _cfg(), _context(FakeBot(), _group_event(), auto=True)
            ),
            [],
        )

    def test_definitions_absent_in_private_chat(self) -> None:
        from nonebot.adapters.onebot.v11 import PrivateMessageEvent

        event = PrivateMessageEvent(
            time=EVENT_TIME,
            self_id=BOT_ID,
            post_type="message",
            message_type="private",
            sub_type="friend",
            message_id=1,
            user_id=USER_ID,
            raw_message="hi",
            font=0,
            sender={"user_id": USER_ID, "nickname": "nick"},
            message=[{"type": "text", "data": {"text": "hi"}}],
        )
        self.assertEqual(group_actions.definitions(_cfg(), _context(FakeBot(), event)), [])

    def test_disabled_tool_is_not_declared(self) -> None:
        cfg = _cfg(tools={"group_kick": {"enabled": False}})
        names = {tool["function"]["name"] for tool in group_actions.definitions(cfg)}
        self.assertNotIn("group_kick", names)
        self.assertIn("group_mute", names)

    def test_schemas_never_expose_group_id(self) -> None:
        for tool in group_actions.definitions(_cfg()):
            function = tool["function"]
            properties = function["parameters"]["properties"]
            self.assertNotIn("group_id", properties, function["name"])
            self.assertFalse(function["parameters"]["additionalProperties"], function["name"])

    def test_registry_declares_group_action_tools(self) -> None:
        names = set()
        for tool in tool_registry.available_tools(_cfg(), _context(FakeBot(), _group_event())):
            function = tool.get("function")
            if isinstance(function, dict) and function.get("name"):
                names.add(str(function["name"]))
        self.assertLessEqual(
            {"group_mute", "group_kick", "group_poke", "group_mention"}, names
        )

    def test_registry_hides_group_action_tools_in_auto_reply(self) -> None:
        names = {
            tool["function"]["name"]
            for tool in tool_registry.available_tools(
                _cfg(), _context(FakeBot(), _group_event(), auto=True)
            )
            if isinstance(tool.get("function"), dict)
        }
        self.assertNotIn("group_mute", names)
        self.assertNotIn("group_poke", names)


class MuteTests(_ResetStateMixin, unittest.IsolatedAsyncioTestCase):
    async def test_admin_can_mute_member(self) -> None:
        bot = FakeBot()
        payload = await _run(
            "group_mute",
            {"user": "小刚"},
            bot=bot,
            event=_group_event(ADMIN_ID, role="admin"),
        )
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["action"], "mute")
        self.assertEqual(payload["duration_seconds"], 600)
        api, kwargs = bot.calls[-1]
        self.assertEqual(api, "set_group_ban")
        self.assertEqual(kwargs["group_id"], GROUP_ID)
        self.assertEqual(kwargs["user_id"], OTHER_ID)
        self.assertEqual(kwargs["duration"], 600)

    async def test_minutes_zero_unmutes(self) -> None:
        bot = FakeBot()
        payload = await _run(
            "group_mute",
            {"user": str(OTHER_ID), "minutes": 0},
            bot=bot,
            event=_group_event(ADMIN_ID, role="admin"),
        )
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["action"], "unmute")
        self.assertEqual(bot.calls[-1][1]["duration"], 0)

    async def test_minutes_are_clamped_to_max(self) -> None:
        bot = FakeBot()
        payload = await _run(
            "group_mute",
            {"user": str(OTHER_ID), "minutes": 999999},
            bot=bot,
            event=_group_event(ADMIN_ID, role="admin"),
        )
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["minutes"], 43200)
        self.assertEqual(bot.calls[-1][1]["duration"], 43200 * 60)

    async def test_regular_member_cannot_mute(self) -> None:
        bot = FakeBot()
        payload = await _run("group_mute", {"user": "小刚"}, bot=bot)
        self.assertFalse(payload["ok"])
        self.assertIn("只有群主 / 管理员", payload["error"])
        self.assertNotIn("set_group_ban", bot.api_names())

    async def test_superuser_can_mute_without_group_admin_role(self) -> None:
        bot = FakeBot()
        cfg = _cfg()
        with patch(
            "core.command_router.load_main_config",
            return_value={"bot": {"superuser_id": str(USER_ID)}},
        ):
            payload = await _run("group_mute", {"user": "小刚"}, bot=bot, cfg=cfg)
        self.assertTrue(payload["ok"], payload)
        self.assertIn("set_group_ban", bot.api_names())

    async def test_requester_admin_check_can_be_disabled(self) -> None:
        bot = FakeBot()
        cfg = _cfg(tools={"group_mute": {"enabled": True, "require_requester_admin": False}})
        payload = await _run("group_mute", {"user": "小刚"}, bot=bot, cfg=cfg)
        self.assertTrue(payload["ok"], payload)
        self.assertIn("set_group_ban", bot.api_names())

    async def test_bot_without_admin_role_is_rejected(self) -> None:
        bot = FakeBot(bot_role="member")
        payload = await _run(
            "group_mute", {"user": "小刚"}, bot=bot, event=_group_event(ADMIN_ID, role="admin")
        )
        self.assertFalse(payload["ok"])
        self.assertIn("机器人不是本群管理员", payload["error"])
        self.assertNotIn("set_group_ban", bot.api_names())

    async def test_cannot_mute_owner(self) -> None:
        payload = await _run(
            "group_mute",
            {"user": str(OWNER_ID)},
            event=_group_event(ADMIN_ID, role="admin"),
        )
        self.assertFalse(payload["ok"])
        self.assertIn("群主", payload["error"])

    async def test_cannot_mute_admin(self) -> None:
        payload = await _run(
            "group_mute",
            {"user": str(ADMIN_ID)},
            event=_group_event(OWNER_ID, role="owner"),
        )
        self.assertFalse(payload["ok"])
        self.assertIn("管理员", payload["error"])

    async def test_cannot_mute_self(self) -> None:
        payload = await _run(
            "group_mute",
            {"user": str(ADMIN_ID)},
            event=_group_event(ADMIN_ID, role="admin"),
        )
        self.assertFalse(payload["ok"])
        self.assertIn("自己", payload["error"])

    async def test_cannot_mute_bot(self) -> None:
        payload = await _run(
            "group_mute",
            {"user": str(BOT_ID)},
            event=_group_event(ADMIN_ID, role="admin"),
        )
        self.assertFalse(payload["ok"])
        self.assertIn("机器人自己", payload["error"])

    async def test_ambiguous_name_returns_candidates(self) -> None:
        members = [_member(OTHER_ID, nickname="小明"), _member(ADMIN_ID, nickname="小明二号")]
        bot = FakeBot(members=members, bot_role="admin")
        payload = await _run(
            "group_mute",
            {"user": "小"},
            bot=bot,
            event=_group_event(ADMIN_ID, role="admin"),
        )
        self.assertFalse(payload["ok"])
        self.assertIn("多位成员", payload["error"])

    async def test_api_failure_is_reported(self) -> None:
        bot = FakeBot()
        bot.fail_api("set_group_ban")
        payload = await _run(
            "group_mute", {"user": "小刚"}, bot=bot, event=_group_event(ADMIN_ID, role="admin")
        )
        self.assertFalse(payload["ok"])
        self.assertIn("禁言失败", payload["error"])

    async def test_auto_reply_is_refused(self) -> None:
        bot = FakeBot()
        payload = await _run(
            "group_mute",
            {"user": "小刚"},
            bot=bot,
            event=_group_event(ADMIN_ID, role="admin"),
            auto=True,
        )
        self.assertFalse(payload["ok"])
        self.assertIn("主动接话", payload["error"])
        self.assertNotIn("set_group_ban", bot.api_names())


class KickTests(_ResetStateMixin, unittest.IsolatedAsyncioTestCase):
    async def test_admin_can_kick_member(self) -> None:
        bot = FakeBot()
        payload = await _run(
            "group_kick", {"user": "小刚"}, bot=bot, event=_group_event(ADMIN_ID, role="admin")
        )
        self.assertTrue(payload["ok"], payload)
        api, kwargs = bot.calls[-1]
        self.assertEqual(api, "set_group_kick")
        self.assertEqual(kwargs["user_id"], OTHER_ID)
        self.assertFalse(kwargs["reject_add_request"])

    async def test_reject_add_request_is_forwarded(self) -> None:
        bot = FakeBot()
        await _run(
            "group_kick",
            {"user": "小刚", "reject_add_request": True},
            bot=bot,
            event=_group_event(ADMIN_ID, role="admin"),
        )
        self.assertTrue(bot.calls[-1][1]["reject_add_request"])

    async def test_admin_cannot_kick_other_admin(self) -> None:
        payload = await _run(
            "group_kick",
            {"user": str(ADMIN_ID)},
            event=_group_event(OTHER_ID, role="admin"),
        )
        self.assertFalse(payload["ok"])
        self.assertIn("只有群主", payload["error"])

    async def test_owner_can_kick_admin(self) -> None:
        bot = FakeBot()
        payload = await _run(
            "group_kick",
            {"user": str(ADMIN_ID)},
            bot=bot,
            event=_group_event(OWNER_ID, role="owner"),
        )
        self.assertTrue(payload["ok"], payload)
        self.assertIn("set_group_kick", bot.api_names())

    async def test_cannot_kick_owner(self) -> None:
        payload = await _run(
            "group_kick",
            {"user": str(OWNER_ID)},
            event=_group_event(ADMIN_ID, role="admin"),
        )
        self.assertFalse(payload["ok"])
        self.assertIn("群主", payload["error"])

    async def test_cannot_kick_self(self) -> None:
        payload = await _run(
            "group_kick",
            {"user": str(ADMIN_ID)},
            event=_group_event(ADMIN_ID, role="admin"),
        )
        self.assertFalse(payload["ok"])
        self.assertIn("自己", payload["error"])

    async def test_regular_member_cannot_kick(self) -> None:
        bot = FakeBot()
        payload = await _run("group_kick", {"user": "小刚"}, bot=bot)
        self.assertFalse(payload["ok"])
        self.assertNotIn("set_group_kick", bot.api_names())


class PokeTests(_ResetStateMixin, unittest.IsolatedAsyncioTestCase):
    async def test_poke_named_member(self) -> None:
        bot = FakeBot()
        payload = await _run("group_poke", {"user": "小刚"}, bot=bot)
        self.assertTrue(payload["ok"], payload)
        api, kwargs = bot.calls[-1]
        self.assertEqual(api, "send_poke")
        self.assertEqual(kwargs["group_id"], GROUP_ID)
        self.assertEqual(kwargs["user_id"], OTHER_ID)

    async def test_poke_defaults_to_requester(self) -> None:
        bot = FakeBot()
        payload = await _run("group_poke", {}, bot=bot)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(bot.calls[-1][1]["user_id"], USER_ID)

    async def test_poke_me_alias(self) -> None:
        bot = FakeBot()
        payload = await _run("group_poke", {"user": "我"}, bot=bot)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(bot.calls[-1][1]["user_id"], USER_ID)

    async def test_poke_you_alias_targets_bot(self) -> None:
        bot = FakeBot()
        payload = await _run("group_poke", {"user": "你"}, bot=bot)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(bot.calls[-1][1]["user_id"], BOT_ID)
        self.assertFalse(any(api == "get_group_member_list" for api in bot.api_names()))

    async def test_poke_is_rate_limited(self) -> None:
        cfg = _cfg(tools={"group_poke": {"enabled": True, "max_per_hour": 2}})
        bot = FakeBot()
        first = await _run("group_poke", {}, bot=bot, cfg=cfg)
        second = await _run("group_poke", {}, bot=bot, cfg=cfg)
        third = await _run("group_poke", {}, bot=bot, cfg=cfg)
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertFalse(third["ok"])
        self.assertIn("频繁", third["error"])
        self.assertEqual(sum(1 for api, _ in bot.calls if api == "send_poke"), 2)

    async def test_poke_zero_limit_disables_rate_limit(self) -> None:
        cfg = _cfg(tools={"group_poke": {"enabled": True, "max_per_hour": 0}})
        bot = FakeBot()
        for _ in range(5):
            payload = await _run("group_poke", {}, bot=bot, cfg=cfg)
            self.assertTrue(payload["ok"], payload)
        self.assertEqual(sum(1 for api, _ in bot.calls if api == "send_poke"), 5)

    async def test_poke_unknown_member(self) -> None:
        payload = await _run("group_poke", {"user": "不存在的人"})
        self.assertFalse(payload["ok"])
        self.assertIn("没有找到", payload["error"])

    async def test_poke_api_failure(self) -> None:
        bot = FakeBot()
        bot.fail_api("send_poke")
        payload = await _run("group_poke", {}, bot=bot)
        self.assertFalse(payload["ok"])
        self.assertIn("戳一戳失败", payload["error"])

    async def test_poke_refused_in_auto_reply(self) -> None:
        bot = FakeBot()
        payload = await _run("group_poke", {}, bot=bot, auto=True)
        self.assertFalse(payload["ok"])


class MentionTests(_ResetStateMixin, unittest.IsolatedAsyncioTestCase):
    async def test_returns_marker_for_member(self) -> None:
        payload = await _run("group_mention", {"user": "小刚"})
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["marker"], f"[[at:{OTHER_ID}]]")
        self.assertEqual(payload["user"]["user_id"], str(OTHER_ID))
        self.assertIn("marker", payload["note"])

    async def test_resolves_by_qq_number(self) -> None:
        payload = await _run("group_mention", {"user": str(OTHER_ID)})
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["marker"], f"[[at:{OTHER_ID}]]")

    async def test_cannot_mention_bot(self) -> None:
        payload = await _run("group_mention", {"user": str(BOT_ID)})
        self.assertFalse(payload["ok"])
        self.assertIn("机器人自己", payload["error"])

    async def test_cannot_mention_all(self) -> None:
        for value in ("all", "全体成员", "全体"):
            payload = await _run("group_mention", {"user": value})
            self.assertFalse(payload["ok"], value)
            self.assertIn("全体成员", payload["error"])

    async def test_unknown_member(self) -> None:
        payload = await _run("group_mention", {"user": "不存在的人"})
        self.assertFalse(payload["ok"])


class DisabledTests(_ResetStateMixin, unittest.IsolatedAsyncioTestCase):
    async def test_disabled_tool_is_not_executable(self) -> None:
        cfg = _cfg(tools={"group_poke": {"enabled": False}})
        payload = await _run("group_poke", {}, cfg=cfg)
        self.assertFalse(payload["ok"])
        self.assertIn("disabled by configuration", payload["error"])

    async def test_registry_rejects_group_action_in_private_chat(self) -> None:
        from nonebot.adapters.onebot.v11 import PrivateMessageEvent

        event = PrivateMessageEvent(
            time=EVENT_TIME,
            self_id=BOT_ID,
            post_type="message",
            message_type="private",
            sub_type="friend",
            message_id=1,
            user_id=USER_ID,
            raw_message="hi",
            font=0,
            sender={"user_id": USER_ID, "nickname": "nick"},
            message=[{"type": "text", "data": {"text": "hi"}}],
        )
        result = await tool_registry.execute_tool_call(
            _cfg(),
            {"id": "call_1", "function": {"name": "group_poke", "arguments": "{}"}},
            _context(FakeBot(), event),
        )
        payload = json.loads(result["content"])
        self.assertIn("unknown or disabled tool", payload["error"])


class CompatHelperTests(unittest.TestCase):
    def test_group_helpers_are_exposed_for_reuse(self) -> None:
        self.assertIs(group_tool.fetch_member_info, group_tool._fetch_member_info)
        self.assertIs(group_tool.resolve_member, group_tool._resolve_member)
        self.assertIs(group_tool.member_view, group_tool._member_view)
        self.assertIs(group_tool.group_id_of, group_tool._group_id)


if __name__ == "__main__":
    unittest.main()
