from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from nonebot.adapters.onebot.v11 import GroupMessageEvent

from plugins.aiagent.review import _as_bool, _parse_json_response
from plugins.group_guard import _group_under_guard, _is_bare_recall
from plugins.group_guard.config import DEFAULT_REVIEW_PROMPT, get_config


def make_group_event(
    group_id: str = "111",
    user_id: str = "222",
    message: list[dict[str, Any]] | None = None,
) -> GroupMessageEvent:
    return GroupMessageEvent(
        time=0,
        self_id="1",
        post_type="message",
        message_type="group",
        sub_type="normal",
        font=0,
        sender={"user_id": user_id},
        user_id=user_id,
        message_id=1,
        raw_message="撤回",
        message=message or [{"type": "text", "data": {"text": "撤回"}}],
        group_id=group_id,
    )


def guard_config(*, require_whitelist: bool, whitelist_groups: list[str], blacklist_groups: list[str] | None = None) -> dict[str, Any]:
    return {
        "review": {"require_group_whitelist": require_whitelist},
        "permissions": {
            "whitelist": {"enable": bool(whitelist_groups), "group": whitelist_groups, "user": []},
            "blacklist": {"enable": bool(blacklist_groups), "group": blacklist_groups or [], "user": []},
        },
    }


class GroupGateTests(unittest.TestCase):
    def test_whitelisted_group_is_reviewed(self) -> None:
        cfg = guard_config(require_whitelist=True, whitelist_groups=["111"])
        self.assertTrue(_group_under_guard(cfg, make_group_event(group_id="111")))

    def test_group_outside_whitelist_is_skipped(self) -> None:
        cfg = guard_config(require_whitelist=True, whitelist_groups=["999"])
        self.assertFalse(_group_under_guard(cfg, make_group_event(group_id="111")))

    def test_empty_whitelist_still_blocks_when_required(self) -> None:
        cfg = guard_config(require_whitelist=True, whitelist_groups=[])
        self.assertFalse(_group_under_guard(cfg, make_group_event(group_id="111")))

    def test_whitelist_not_required_falls_back_to_access_rules(self) -> None:
        cfg = guard_config(require_whitelist=False, whitelist_groups=[])
        self.assertTrue(_group_under_guard(cfg, make_group_event(group_id="111")))

    def test_blacklisted_group_is_skipped(self) -> None:
        cfg = guard_config(require_whitelist=False, whitelist_groups=[], blacklist_groups=["111"])
        self.assertFalse(_group_under_guard(cfg, make_group_event(group_id="111")))


class BareRecallTests(unittest.TestCase):
    def test_plain_text_only_is_bare(self) -> None:
        self.assertTrue(_is_bare_recall(make_group_event(), ""))

    def test_at_segment_breaks_bare_condition(self) -> None:
        event = make_group_event(
            message=[{"type": "at", "data": {"qq": "333"}}, {"type": "text", "data": {"text": "撤回"}}]
        )
        self.assertFalse(_is_bare_recall(event, ""))

    def test_extra_args_break_bare_condition(self) -> None:
        self.assertFalse(_is_bare_recall(make_group_event(), "这条"))


class ParseJsonTests(unittest.TestCase):
    def test_plain_json(self) -> None:
        self.assertEqual(_parse_json_response('{"risk": true, "reason": "x"}'), {"risk": True, "reason": "x"})

    def test_fenced_json(self) -> None:
        parsed = _parse_json_response('```json\n{"risk": false}\n```')
        self.assertEqual(parsed, {"risk": False})

    def test_json_embedded_in_prose(self) -> None:
        parsed = _parse_json_response('判定结果如下：{"risk": true, "reason": "y"} 完毕')
        self.assertEqual(parsed, {"risk": True, "reason": "y"})

    def test_garbage_returns_none(self) -> None:
        self.assertIsNone(_parse_json_response("无法判断"))

    def test_non_object_returns_none(self) -> None:
        self.assertIsNone(_parse_json_response("[1, 2]"))

    def test_as_bool_accepts_string_forms(self) -> None:
        self.assertTrue(_as_bool("true"))
        self.assertTrue(_as_bool("是"))
        self.assertFalse(_as_bool("false"))
        self.assertFalse(_as_bool(None))


class ConfigTests(unittest.TestCase):
    def test_blank_prompt_falls_back_to_default(self) -> None:
        with patch("plugins.group_guard.config.load_plugin_config", return_value={"review": {"prompt": "   "}}):
            self.assertEqual(get_config()["review"]["prompt"], DEFAULT_REVIEW_PROMPT)

    def test_numeric_fields_are_clamped(self) -> None:
        raw = {"review": {"max_concurrent": 9999, "timeout_seconds": 1, "temperature": 9.0}}
        with patch("plugins.group_guard.config.load_plugin_config", return_value=raw):
            review = get_config()["review"]
        self.assertEqual(review["max_concurrent"], 16)
        self.assertEqual(review["timeout_seconds"], 5)
        self.assertEqual(review["temperature"], 2.0)

    def test_defaults_keep_guard_disabled(self) -> None:
        with patch("plugins.group_guard.config.load_plugin_config", return_value={}):
            cfg = get_config()
        self.assertFalse(cfg["enabled"])
        self.assertFalse(cfg["review"]["enabled"])
        self.assertTrue(cfg["review"]["require_group_whitelist"])
        self.assertTrue(cfg["recall_command"]["other_requires_admin"])


if __name__ == "__main__":
    unittest.main()
