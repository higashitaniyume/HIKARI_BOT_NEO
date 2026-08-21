"""后台 AI 配置文件 / 会话绑定 API 层测试。

重点：CRUD 的参数校验、绑定读写、以及 API Key 掩码不跨配置文件泄漏。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import nonebot

# plugins.bot_admin 的导入链会走到 astrbot_compat 的 get_driver()，
# 单独跑这个文件时需要先把 NoneBot 初始化出来。
try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="nonebot.drivers.none:Driver")

from plugins.aiagent import config as aiagent_config
from plugins.bot_admin import settings as admin_settings


class AdminProfileApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_path = aiagent_config.CONFIG_PATH
        aiagent_config.CONFIG_PATH = Path(self._tmpdir.name) / "aiagent.json"
        aiagent_config.ensure_config()

    def tearDown(self) -> None:
        aiagent_config.CONFIG_PATH = self._orig_path
        self._tmpdir.cleanup()

    # ── GET 状态 ─────────────────────────────────────────────────────────

    def test_config_state_includes_profiles_and_bindings(self) -> None:
        state = admin_settings._aiagent_config_state()
        self.assertEqual(state["active_profile"], "default")
        self.assertEqual(state["editing_profile"], "default")
        self.assertEqual([item["id"] for item in state["profiles"]], ["default"])
        self.assertEqual(state["bindings"], {"group": {}, "private": {}})
        self.assertIn("tools_catalog", state)

    def test_config_state_never_returns_api_key(self) -> None:
        admin_settings._update_aiagent_config({"model": {"api_key": "secret-default"}})
        state = admin_settings._aiagent_config_state()
        self.assertEqual(state["config"]["model"]["api_key"], "")
        self.assertTrue(state["config"]["model"]["api_key_set"])

    def test_api_key_mask_is_per_profile(self) -> None:
        admin_settings._update_aiagent_config({"model": {"api_key": "secret-default"}})
        created = admin_settings._create_aiagent_profile({"name": "无 Key 配置"})
        new_id = created["editing_profile"]

        fresh = admin_settings._aiagent_config_state(new_id)
        self.assertFalse(fresh["config"]["model"]["api_key_set"])
        self.assertEqual(fresh["config"]["model"]["api_key"], "")
        # 另一套配置的 key 不受影响，也不会出现在响应里。
        default_state = admin_settings._aiagent_config_state("default")
        self.assertTrue(default_state["config"]["model"]["api_key_set"])
        self.assertEqual(default_state["config"]["model"]["api_key"], "")
        self.assertEqual(aiagent_config.get_config("default")["model"]["api_key"], "secret-default")
        self.assertEqual(aiagent_config.get_config(new_id)["model"]["api_key"], "")

    def test_profile_summaries_do_not_leak_api_key(self) -> None:
        admin_settings._update_aiagent_config({"model": {"api_key": "secret-default"}})
        for summary in admin_settings._aiagent_config_state()["profiles"]:
            self.assertNotIn("api_key", summary)
            self.assertIn("api_key_set", summary)

    # ── 保存 ─────────────────────────────────────────────────────────────

    def test_save_targets_requested_profile_only(self) -> None:
        created = admin_settings._create_aiagent_profile({"name": "备用"})
        other_id = created["editing_profile"]
        admin_settings._update_aiagent_config({"model": {"model": "other-model"}}, other_id)

        self.assertEqual(aiagent_config.get_config(other_id)["model"]["model"], "other-model")
        self.assertEqual(
            aiagent_config.get_config("default")["model"]["model"],
            aiagent_config.DEFAULT_CONFIG["model"]["model"],
        )

    def test_quota_save_does_not_touch_profiles(self) -> None:
        admin_settings._update_aiagent_config({"model": {"api_key": "keep-me"}})
        admin_settings._update_aiagent_quota({"quota": {"enabled": True, "default_user": {"daily": 7}}})

        cfg = aiagent_config.get_config()
        self.assertTrue(cfg["quota"]["enabled"])
        self.assertEqual(cfg["quota"]["default_user"]["daily"], 7)
        self.assertEqual(cfg["model"]["api_key"], "keep-me")

    # ── CRUD ─────────────────────────────────────────────────────────────

    def test_create_switches_editing_target(self) -> None:
        payload = admin_settings._create_aiagent_profile({"name": "Nightly"})
        self.assertEqual(payload["editing_profile"], "nightly")
        self.assertEqual(payload["active_profile"], "default")
        self.assertIn("nightly", [item["id"] for item in payload["profiles"]])
        self.assertIn("Nightly", payload["message"])

    def test_create_copy_from_clones_api_key(self) -> None:
        admin_settings._update_aiagent_config({"model": {"api_key": "secret-default"}})
        payload = admin_settings._create_aiagent_profile({"name": "克隆", "copy_from": "default"})
        cloned_id = payload["editing_profile"]
        self.assertTrue(payload["config"]["model"]["api_key_set"])
        self.assertEqual(aiagent_config.get_config(cloned_id)["model"]["api_key"], "secret-default")

    def test_create_rejects_empty_name(self) -> None:
        with self.assertRaises(ValueError):
            admin_settings._create_aiagent_profile({"name": "   "})

    def test_rename_requires_profile_and_name(self) -> None:
        created = admin_settings._create_aiagent_profile({"name": "旧名"})
        payload = admin_settings._rename_aiagent_profile({"profile": created["editing_profile"], "name": "新名"})
        names = {item["id"]: item["name"] for item in payload["profiles"]}
        self.assertEqual(names[created["editing_profile"]], "新名")

        with self.assertRaises(ValueError):
            admin_settings._rename_aiagent_profile({"profile": "", "name": "x"})
        with self.assertRaises(ValueError):
            admin_settings._rename_aiagent_profile({"profile": created["editing_profile"], "name": ""})
        with self.assertRaises(ValueError):
            admin_settings._rename_aiagent_profile({"profile": "ghost", "name": "x"})

    def test_activate_changes_default(self) -> None:
        created = admin_settings._create_aiagent_profile({"name": "新默认"})
        payload = admin_settings._activate_aiagent_profile({"profile": created["editing_profile"]})
        self.assertEqual(payload["active_profile"], created["editing_profile"])

        with self.assertRaises(ValueError):
            admin_settings._activate_aiagent_profile({"profile": ""})
        with self.assertRaises(ValueError):
            admin_settings._activate_aiagent_profile({"profile": "ghost"})

    def test_delete_requires_non_active_profile(self) -> None:
        created = admin_settings._create_aiagent_profile({"name": "待删"})
        target = created["editing_profile"]
        admin_settings._save_aiagent_binding({"kind": "group", "id": "111", "profile": target})

        payload = admin_settings._delete_aiagent_profile({"profile": target})
        self.assertEqual([item["id"] for item in payload["profiles"]], ["default"])
        self.assertEqual(payload["bindings"]["group"], {})

        with self.assertRaises(ValueError):
            admin_settings._delete_aiagent_profile({"profile": "default"})
        with self.assertRaises(ValueError):
            admin_settings._delete_aiagent_profile({"profile": ""})

    # ── 绑定 ─────────────────────────────────────────────────────────────

    def test_binding_save_and_clear(self) -> None:
        created = admin_settings._create_aiagent_profile({"name": "群专用"})
        target = created["editing_profile"]

        bound = admin_settings._save_aiagent_binding({"kind": "group", "id": "111", "profile": target})
        self.assertEqual(bound["bindings"]["group"], {"111": target})
        counts = {item["id"]: item["bound_count"] for item in bound["profiles"]}
        self.assertEqual(counts[target], 1)

        cleared = admin_settings._save_aiagent_binding({"kind": "group", "id": "111", "profile": ""})
        self.assertEqual(cleared["bindings"]["group"], {})
        self.assertIn("解除", cleared["message"])

    def test_binding_keeps_editing_profile(self) -> None:
        created = admin_settings._create_aiagent_profile({"name": "编辑中"})
        target = created["editing_profile"]
        payload = admin_settings._save_aiagent_binding(
            {"kind": "private", "id": "333", "profile": "default", "editing": target}
        )
        self.assertEqual(payload["editing_profile"], target)
        self.assertEqual(payload["bindings"]["private"], {"333": "default"})

    def test_binding_rejects_invalid_payload(self) -> None:
        with self.assertRaises(ValueError):
            admin_settings._save_aiagent_binding({"kind": "channel", "id": "1", "profile": "default"})
        with self.assertRaises(ValueError):
            admin_settings._save_aiagent_binding({"kind": "group", "id": "", "profile": "default"})
        with self.assertRaises(ValueError):
            admin_settings._save_aiagent_binding({"kind": "group", "id": "abc", "profile": "default"})
        with self.assertRaises(ValueError):
            admin_settings._save_aiagent_binding({"kind": "group", "id": "111", "profile": "ghost"})


if __name__ == "__main__":
    unittest.main()
