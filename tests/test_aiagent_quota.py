from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent

from core.access_control import is_event_allowed
from plugins.aiagent import quota as quota_mod


def make_group_event(group_id: str = "111", user_id: str = "222") -> GroupMessageEvent:
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
        raw_message="hi",
        message=[{"type": "text", "data": {"text": "hi"}}],
        group_id=group_id,
    )


def make_private_event(user_id: str = "333") -> PrivateMessageEvent:
    return PrivateMessageEvent(
        time=0,
        self_id="1",
        post_type="message",
        message_type="private",
        sub_type="friend",
        font=0,
        sender={"user_id": user_id},
        user_id=user_id,
        message_id=2,
        raw_message="hi",
        message=[{"type": "text", "data": {"text": "hi"}}],
    )


def make_cfg() -> dict:
    return {
        "quota": {
            "enabled": True,
            "default_user": {"daily": 100, "hourly": 10},
            "default_group": {"daily": 300, "hourly": 30},
            "user_overrides": {},
            "group_overrides": {},
            "exempt_user_ids": [],
            "exempt_group_ids": [],
            "count_background": True,
        }
    }


class QuotaTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_path = quota_mod.QUOTA_PATH
        quota_mod.QUOTA_PATH = Path(self._tmpdir.name) / "aiagent_quota.json"
        quota_mod.reset_usage_state()
        quota_mod._load_usage()

    def tearDown(self) -> None:
        quota_mod.QUOTA_PATH = self._orig_path
        quota_mod.reset_usage_state()
        self._tmpdir.cleanup()


class LimitResolutionTests(QuotaTestBase):
    def test_defaults_applied(self) -> None:
        cfg = make_cfg()
        self.assertEqual(quota_mod._limits_for(cfg, "user:333"), {"daily": 100, "hourly": 10})
        self.assertEqual(quota_mod._limits_for(cfg, "group:111"), {"daily": 300, "hourly": 30})

    def test_override_wins(self) -> None:
        cfg = make_cfg()
        cfg["quota"]["user_overrides"] = {"333": {"daily": 999, "hourly": 7}}
        cfg["quota"]["group_overrides"] = {"111": {"daily": 0, "hourly": 0}}
        self.assertEqual(quota_mod._limits_for(cfg, "user:333"), {"daily": 999, "hourly": 7})
        self.assertEqual(quota_mod._limits_for(cfg, "group:111"), {"daily": 0, "hourly": 0})

    def test_override_missing_field_falls_back_to_default(self) -> None:
        cfg = make_cfg()
        cfg["quota"]["user_overrides"] = {"333": {"hourly": 7}}
        self.assertEqual(quota_mod._limits_for(cfg, "user:333"), {"daily": 100, "hourly": 7})

    def test_window_keys(self) -> None:
        now = datetime(2026, 8, 2, 15, 30)
        self.assertEqual(quota_mod._window_keys(now), ("2026-08-02", "2026-08-02-15"))


class CheckQuotaTests(QuotaTestBase):
    def test_disabled_always_allows(self) -> None:
        cfg = make_cfg()
        cfg["quota"]["enabled"] = False
        self.assertIsNone(quota_mod.check_quota(cfg, make_group_event()))

    def test_within_limits_allows(self) -> None:
        cfg = make_cfg()
        cfg["quota"]["default_group"] = {"daily": 10, "hourly": 100}
        event = make_group_event()
        scope = quota_mod.scope_for_event(event)
        with patch.object(quota_mod, "_window_keys", return_value=("2026-08-02", "2026-08-02-15")):
            quota_mod._record(scope, "day", "2026-08-02", 5)
            self.assertIsNone(quota_mod.check_quota(cfg, event))

    def test_daily_block(self) -> None:
        cfg = make_cfg()
        cfg["quota"]["default_group"] = {"daily": 10, "hourly": 100}
        event = make_group_event()
        scope = quota_mod.scope_for_event(event)
        with patch.object(quota_mod, "_window_keys", return_value=("2026-08-02", "2026-08-02-15")):
            quota_mod._record(scope, "day", "2026-08-02", 10)
            block = quota_mod.check_quota(cfg, event)
        self.assertIsNotNone(block)
        self.assertEqual(block["who"], "group")
        self.assertEqual(block["period"], "day")
        self.assertEqual(block["used"], 10)
        self.assertEqual(block["limit"], 10)

    def test_hourly_block(self) -> None:
        cfg = make_cfg()
        cfg["quota"]["default_group"] = {"daily": 1000, "hourly": 30}
        event = make_group_event()
        scope = quota_mod.scope_for_event(event)
        with patch.object(quota_mod, "_window_keys", return_value=("2026-08-02", "2026-08-02-15")):
            quota_mod._record(scope, "hour", "2026-08-02-15", 30)
            block = quota_mod.check_quota(cfg, event)
        self.assertIsNotNone(block)
        self.assertEqual(block["period"], "hour")
        self.assertEqual(block["who"], "group")

    def test_zero_limit_is_unlimited(self) -> None:
        cfg = make_cfg()
        cfg["quota"]["default_user"] = {"daily": 0, "hourly": 0}
        event = make_private_event()
        scope = quota_mod.scope_for_event(event)
        with patch.object(quota_mod, "_window_keys", return_value=("2026-08-02", "2026-08-02-15")):
            quota_mod._record(scope, "day", "2026-08-02", 10**9)
            quota_mod._record(scope, "hour", "2026-08-02-15", 10**9)
            self.assertIsNone(quota_mod.check_quota(cfg, event))

    def test_exempt_user_always_allows(self) -> None:
        cfg = make_cfg()
        cfg["quota"]["default_user"] = {"daily": 1, "hourly": 1}
        cfg["quota"]["exempt_user_ids"] = ["333"]
        event = make_private_event("333")
        with patch.object(quota_mod, "_window_keys", return_value=("2026-08-02", "2026-08-02-15")):
            quota_mod._record(quota_mod.scope_for_event(event), "day", "2026-08-02", 10**9)
            self.assertIsNone(quota_mod.check_quota(cfg, event))

    def test_exempt_group_always_allows(self) -> None:
        cfg = make_cfg()
        cfg["quota"]["default_group"] = {"daily": 1, "hourly": 1}
        cfg["quota"]["exempt_group_ids"] = ["111"]
        event = make_group_event("111")
        with patch.object(quota_mod, "_window_keys", return_value=("2026-08-02", "2026-08-02-15")):
            quota_mod._record(quota_mod.scope_for_event(event), "day", "2026-08-02", 10**9)
            self.assertIsNone(quota_mod.check_quota(cfg, event))


class RecordUsageTests(QuotaTestBase):
    def test_record_and_status(self) -> None:
        cfg = make_cfg()
        event = make_group_event()
        quota_mod.record_usage(cfg, event, 1)
        status = quota_mod.get_quota_status(cfg, event)
        self.assertEqual(status["scope"], "group:111")
        self.assertEqual(status["daily"]["used"], 1)
        self.assertEqual(status["hourly"]["used"], 1)
        self.assertEqual(status["daily"]["limit"], 300)
        self.assertEqual(status["daily"]["remaining"], 299)

    def test_default_record_is_one_conversation(self) -> None:
        cfg = make_cfg()
        event = make_private_event()
        quota_mod.record_usage(cfg, event)
        quota_mod.record_usage(cfg, event)
        status = quota_mod.get_quota_status(cfg, event)
        self.assertEqual(status["daily"]["used"], 2)

    def test_non_positive_count_not_recorded(self) -> None:
        cfg = make_cfg()
        event = make_group_event()
        quota_mod.record_usage(cfg, event, 0)
        quota_mod.record_usage(cfg, event, -5)
        status = quota_mod.get_quota_status(cfg, event)
        self.assertEqual(status["daily"]["used"], 0)

    def test_disabled_not_recorded(self) -> None:
        cfg = make_cfg()
        cfg["quota"]["enabled"] = False
        event = make_group_event()
        quota_mod.record_usage(cfg, event, 1)
        status = quota_mod.get_quota_status(cfg, event)
        self.assertEqual(status["daily"]["used"], 0)

    def test_exempt_not_recorded(self) -> None:
        cfg = make_cfg()
        cfg["quota"]["exempt_user_ids"] = ["333"]
        event = make_private_event("333")
        quota_mod.record_usage(cfg, event, 1)
        status = quota_mod.get_quota_status(cfg, event)
        self.assertTrue(status["exempt"])
        self.assertEqual(status["daily"]["used"], 0)

    def test_window_rollover_resets_usage(self) -> None:
        cfg = make_cfg()
        event = make_group_event()
        with patch.object(quota_mod, "_window_keys", return_value=("2026-08-02", "2026-08-02-15")):
            quota_mod.record_usage(cfg, event, 1)
            status = quota_mod.get_quota_status(cfg, event)
        self.assertEqual(status["daily"]["used"], 1)
        self.assertEqual(status["hourly"]["used"], 1)

        # 跨小时：日桶保留，时桶归零
        with patch.object(quota_mod, "_window_keys", return_value=("2026-08-02", "2026-08-02-16")):
            status = quota_mod.get_quota_status(cfg, event)
        self.assertEqual(status["daily"]["used"], 1)
        self.assertEqual(status["hourly"]["used"], 0)

        # 跨天：日桶归零
        with patch.object(quota_mod, "_window_keys", return_value=("2026-08-03", "2026-08-03-00")):
            status = quota_mod.get_quota_status(cfg, event)
        self.assertEqual(status["daily"]["used"], 0)
        self.assertEqual(status["hourly"]["used"], 0)

    def test_persistence_across_reload(self) -> None:
        cfg = make_cfg()
        event = make_private_event()
        quota_mod.record_usage(cfg, event, 1)
        # 模拟重启：清空内存后从磁盘重载
        quota_mod.reset_usage_state()
        quota_mod._load_usage()
        status = quota_mod.get_quota_status(cfg, event)
        self.assertEqual(status["daily"]["used"], 1)
        self.assertEqual(status["hourly"]["used"], 1)

    def test_legacy_tokens_field_compatible(self) -> None:
        """旧版（token 配额时代）账本数据按次数读取兼容。"""
        cfg = make_cfg()
        event = make_group_event()
        scope = quota_mod.scope_for_event(event)
        with patch.object(quota_mod, "_window_keys", return_value=("2026-08-02", "2026-08-02-15")):
            quota_mod._usage[scope] = {
                "day": {"k": "2026-08-02", "tokens": 12345},
                "hour": {"k": "2026-08-02-15", "tokens": 67},
            }
            status = quota_mod.get_quota_status(cfg, event)
        self.assertEqual(status["daily"]["used"], 12345)
        self.assertEqual(status["hourly"]["used"], 67)

    def test_reset_scope(self) -> None:
        cfg = make_cfg()
        event = make_group_event()
        quota_mod.record_usage(cfg, event, 1)
        self.assertTrue(quota_mod.reset_scope("group:111"))
        self.assertFalse(quota_mod.reset_scope("group:999"))
        status = quota_mod.get_quota_status(cfg, event)
        self.assertEqual(status["daily"]["used"], 0)

    def test_reset_all(self) -> None:
        cfg = make_cfg()
        quota_mod.record_usage(cfg, make_group_event(), 1)
        quota_mod.record_usage(cfg, make_private_event(), 1)
        self.assertEqual(quota_mod.reset_all_usage(), 2)
        self.assertEqual(quota_mod.get_all_usage(), {})

    def test_get_all_usage_keys(self) -> None:
        cfg = make_cfg()
        quota_mod.record_usage(cfg, make_group_event(), 1)
        quota_mod.record_usage(cfg, make_private_event(), 1)
        usage = quota_mod.get_all_usage()
        self.assertIn("group:111", usage)
        self.assertIn("user:333", usage)


class AccessControlTests(QuotaTestBase):
    """配额页保存的黑白名单与准入检查集成。"""

    def _cfg_with_permissions(self, permissions: dict) -> dict:
        cfg = make_cfg()
        cfg["permissions"] = permissions
        return cfg

    def test_default_allows(self) -> None:
        cfg = self._cfg_with_permissions({})
        self.assertTrue(is_event_allowed(cfg, make_group_event()))
        self.assertTrue(is_event_allowed(cfg, make_private_event()))

    def test_blacklist_user_blocks(self) -> None:
        cfg = self._cfg_with_permissions({
            "blacklist": {"enable": True, "user": ["222"], "group": []},
        })
        self.assertFalse(is_event_allowed(cfg, make_group_event(user_id="222")))
        self.assertTrue(is_event_allowed(cfg, make_group_event(user_id="888")))

    def test_blacklist_group_blocks(self) -> None:
        cfg = self._cfg_with_permissions({
            "blacklist": {"enable": True, "user": [], "group": ["111"]},
        })
        self.assertFalse(is_event_allowed(cfg, make_group_event("111")))
        self.assertTrue(is_event_allowed(cfg, make_group_event("999")))

    def test_whitelist_blocks_outsiders(self) -> None:
        cfg = self._cfg_with_permissions({
            "whitelist": {"enable": True, "user": ["222"], "group": []},
        })
        self.assertTrue(is_event_allowed(cfg, make_group_event(user_id="222")))
        self.assertFalse(is_event_allowed(cfg, make_group_event(user_id="888")))

    def test_whitelist_group_allows_members(self) -> None:
        cfg = self._cfg_with_permissions({
            "whitelist": {"enable": True, "user": [], "group": ["111"]},
        })
        self.assertTrue(is_event_allowed(cfg, make_group_event("111")))
        self.assertFalse(is_event_allowed(cfg, make_group_event("999")))

    def test_admin_always_allowed(self) -> None:
        cfg = self._cfg_with_permissions({
            "admin_id": "666",
            "blacklist": {"enable": True, "user": ["666"], "group": []},
        })
        self.assertTrue(is_event_allowed(cfg, make_private_event("666")))

    def test_dimension_switches_are_independent(self) -> None:
        """只开用户白名单：私聊/群聊都只认用户名单，群维度不生效。"""
        cfg = self._cfg_with_permissions({
            "whitelist": {
                "enable": True,
                "user_enable": True,
                "group_enable": False,
                "user": ["222"],
                "group": ["111"],
            },
        })
        # 名单内用户放行（私聊与群聊）
        self.assertTrue(is_event_allowed(cfg, make_private_event("222")))
        self.assertTrue(is_event_allowed(cfg, make_group_event("111", user_id="222")))
        # 名单外用户拒绝（即使所在群在白名单里——群维度未启用）
        self.assertFalse(is_event_allowed(cfg, make_private_event("333")))
        self.assertFalse(is_event_allowed(cfg, make_group_event("111", user_id="333")))

    def test_group_whitelist_only(self) -> None:
        """只开群白名单：名单内群放行（成员无需在用户名单），名单外拒绝；私聊不受群白名单影响。"""
        cfg = self._cfg_with_permissions({
            "whitelist": {
                "enable": True,
                "user_enable": False,
                "group_enable": True,
                "user": [],
                "group": ["111"],
            },
        })
        self.assertTrue(is_event_allowed(cfg, make_group_event("111", user_id="333")))
        self.assertFalse(is_event_allowed(cfg, make_group_event("999", user_id="222")))
        # 群白名单只管群聊：私聊没有群号，不受约束
        self.assertTrue(is_event_allowed(cfg, make_private_event("222")))

    def test_user_blacklist_only(self) -> None:
        """只开用户黑名单：名单内用户被拒（私聊与群聊），群维度不受影响。"""
        cfg = self._cfg_with_permissions({
            "blacklist": {
                "enable": True,
                "user_enable": True,
                "group_enable": False,
                "user": ["222"],
                "group": ["111"],
            },
        })
        self.assertFalse(is_event_allowed(cfg, make_private_event("222")))
        self.assertFalse(is_event_allowed(cfg, make_group_event("111", user_id="222")))
        self.assertTrue(is_event_allowed(cfg, make_group_event("111", user_id="333")))
        self.assertTrue(is_event_allowed(cfg, make_private_event("333")))

    def test_group_blacklist_only(self) -> None:
        """只开群黑名单：名单内群被拒，其他群与私聊不受影响。"""
        cfg = self._cfg_with_permissions({
            "blacklist": {
                "enable": True,
                "user_enable": False,
                "group_enable": True,
                "user": ["222"],
                "group": ["111"],
            },
        })
        self.assertFalse(is_event_allowed(cfg, make_group_event("111")))
        self.assertTrue(is_event_allowed(cfg, make_group_event("999")))
        self.assertTrue(is_event_allowed(cfg, make_private_event("222")))

    def test_whitelist_or_semantics_between_dimensions(self) -> None:
        """用户白名单 + 群白名单：命中任一维度即放行。"""
        cfg = self._cfg_with_permissions({
            "whitelist": {
                "enable": True,
                "user_enable": True,
                "group_enable": True,
                "user": ["222"],
                "group": ["111"],
            },
        })
        # 用户命中（群未命中）
        self.assertTrue(is_event_allowed(cfg, make_group_event("999", user_id="222")))
        # 群命中（用户未命中）
        self.assertTrue(is_event_allowed(cfg, make_group_event("111", user_id="333")))
        # 都未命中
        self.assertFalse(is_event_allowed(cfg, make_group_event("999", user_id="333")))
        self.assertFalse(is_event_allowed(cfg, make_private_event("333")))

    def test_legacy_enable_field_keeps_old_behavior(self) -> None:
        """旧配置（只有 enable，无维度开关）行为与旧逻辑一致。"""
        # 白名单整体开：名单外拒绝
        cfg = self._cfg_with_permissions({
            "whitelist": {"enable": True, "user": ["222"], "group": ["111"]},
        })
        self.assertTrue(is_event_allowed(cfg, make_private_event("222")))
        self.assertTrue(is_event_allowed(cfg, make_group_event("111", user_id="333")))
        self.assertFalse(is_event_allowed(cfg, make_private_event("333")))
        # 用户命中白名单（即使群不在名单）→ 放行；两者都未命中 → 拒绝
        self.assertTrue(is_event_allowed(cfg, make_group_event("999", user_id="222")))
        self.assertFalse(is_event_allowed(cfg, make_group_event("999", user_id="888")))
        # 黑名单整体开：名单内拒绝
        cfg = self._cfg_with_permissions({
            "blacklist": {"enable": True, "user": ["222"], "group": ["111"]},
        })
        self.assertFalse(is_event_allowed(cfg, make_private_event("222")))
        self.assertFalse(is_event_allowed(cfg, make_group_event("111", user_id="222")))
        self.assertTrue(is_event_allowed(cfg, make_private_event("333")))
        # 群不在名单且用户不在名单 → 放行
        self.assertTrue(is_event_allowed(cfg, make_group_event("999", user_id="888")))

    def test_normalize_preserves_dimension_switches(self) -> None:
        from core.access_control import normalize_access_rules

        rules = normalize_access_rules({
            "whitelist": {
                "enable": True,
                "user_enable": False,
                "group_enable": True,
                "user": ["222"],
                "group": [],
            },
        })
        self.assertIs(rules["whitelist"]["user_enable"], False)
        self.assertIs(rules["whitelist"]["group_enable"], True)
        # 未提供的维度开关不写入（判定回退 enable）
        rules2 = normalize_access_rules({"whitelist": {"enable": True}})
        self.assertNotIn("user_enable", rules2["whitelist"])
        self.assertNotIn("group_enable", rules2["whitelist"])

    def test_quota_page_save_persists_permissions(self) -> None:
        """模拟配额页保存：permissions 一并写入配置并可再次读出。

        使用临时 cwd，避免写入真实 BotData 配置文件。
        """
        import os
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = Path.cwd()
            os.chdir(tmp)
            try:
                from plugins.bot_admin.settings import _update_aiagent_quota
                from plugins.aiagent.config import get_config

                state = _update_aiagent_quota({
                    "quota": {"enabled": True},
                    "permissions": {
                        "blacklist": {"enable": True, "user": ["222"], "group": []},
                    },
                })
                saved = state["permissions"]
                self.assertTrue(saved["blacklist"]["enable"])
                self.assertIn("222", saved["blacklist"]["user"])
                # 配置文件里确实写入了 permissions
                cfg = get_config()
                self.assertIn("222", cfg["permissions"]["blacklist"]["user"])
                # 未传 permissions 时（如 AI 设置页保存）不应清空已有黑白名单
                from plugins.bot_admin.settings import _update_aiagent_config

                _update_aiagent_config({
                    "enabled": True,
                    "model": {"base_url": "http://x/v1", "model": "m"},
                })
                cfg = get_config()
                self.assertIn("222", cfg["permissions"]["blacklist"]["user"])
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
