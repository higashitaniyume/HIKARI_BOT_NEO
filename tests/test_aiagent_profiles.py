"""AI Agent 多配置文件（profiles）的配置层测试。

覆盖：旧扁平配置迁移、get_config() 形状不变、绑定解析优先级、
删除配置文件连带清理绑定、拒绝删最后一个 / 当前默认、ID slug 生成与去重。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import nonebot

# profile_commands 经由 core.command_router 用到 nonebot.on_message，
# 单独跑这个文件时需要先把 NoneBot 初始化出来。
try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="nonebot.drivers.none:Driver")

from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent

from core.bot_messages import DEFAULT_MESSAGES
from plugins.aiagent import config as aiagent_config
from plugins.aiagent import profile_commands


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


class ProfileTestBase(unittest.TestCase):
    """每个测试用独立的临时配置文件，互不影响。"""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_path = aiagent_config.CONFIG_PATH
        aiagent_config.CONFIG_PATH = Path(self._tmpdir.name) / "aiagent.json"

    def tearDown(self) -> None:
        aiagent_config.CONFIG_PATH = self._orig_path
        self._tmpdir.cleanup()

    def write_raw(self, data: dict) -> None:
        aiagent_config.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        aiagent_config.CONFIG_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def read_raw(self) -> dict:
        return json.loads(aiagent_config.CONFIG_PATH.read_text(encoding="utf-8"))


class LegacyMigrationTests(ProfileTestBase):
    def test_flat_config_migrates_into_default_profile(self) -> None:
        self.write_raw(
            {
                "enabled": True,
                "model": {"api_key": "legacy-key", "model": "legacy-model"},
                "persona": {"skill_path": "BotData/agent_personas/legacy"},
                "quota": {"enabled": True},
                "permissions": {"blacklist": {"enable": True, "user": ["9"]}},
            }
        )

        aiagent_config.ensure_config()
        doc = self.read_raw()

        self.assertEqual(list(doc["profiles"]), ["default"])
        self.assertEqual(doc["active_profile"], "default")
        self.assertEqual(doc["profiles"]["default"]["name"], "默认配置")
        self.assertEqual(doc["profiles"]["default"]["model"]["api_key"], "legacy-key")
        self.assertEqual(
            doc["profiles"]["default"]["persona"]["skill_path"],
            "BotData/agent_personas/legacy",
        )
        # 全局段留在顶层，旧的扁平段被搬走。
        self.assertTrue(doc["enabled"])
        self.assertTrue(doc["quota"]["enabled"])
        self.assertEqual(doc["permissions"]["blacklist"]["user"], ["9"])
        self.assertNotIn("model", doc)
        self.assertNotIn("persona", doc)

    def test_migration_is_idempotent(self) -> None:
        self.write_raw({"model": {"api_key": "k"}})
        aiagent_config.ensure_config()
        first = self.read_raw()
        first_mtime = aiagent_config.CONFIG_PATH.stat().st_mtime_ns

        aiagent_config.ensure_config()
        self.assertEqual(self.read_raw(), first)
        # 幂等的第二次不应该重写文件（每条聊天消息都会走一次）。
        self.assertEqual(aiagent_config.CONFIG_PATH.stat().st_mtime_ns, first_mtime)

    def test_missing_file_creates_multi_profile_document(self) -> None:
        aiagent_config.ensure_config()
        doc = self.read_raw()
        self.assertEqual(doc["active_profile"], "default")
        self.assertEqual(doc["bindings"], {"group": {}, "private": {}})


class EffectiveConfigShapeTests(ProfileTestBase):
    def test_get_config_keeps_flat_shape(self) -> None:
        aiagent_config.ensure_config()
        cfg = aiagent_config.get_config()

        for key in aiagent_config.PROFILE_KEYS + aiagent_config.GLOBAL_KEYS:
            self.assertIn(key, cfg, f"有效配置缺少 {key} 段")
        # 老消费方读的都是扁平路径。
        self.assertIn("api_key", cfg["model"])
        self.assertIn("plugin_tools", cfg["tools"])
        self.assertEqual(cfg["_profile_id"], "default")
        self.assertEqual(cfg["_profile_name"], "默认配置")
        # profiles/bindings 这些文档级字段不该漏进扁平配置。
        self.assertNotIn("profiles", cfg)
        self.assertNotIn("bindings", cfg)

    def test_get_config_for_specific_profile(self) -> None:
        aiagent_config.ensure_config()
        summary = aiagent_config.create_profile("测试配置")
        aiagent_config.save_config({"model": {"model": "other-model"}}, summary["id"])

        self.assertEqual(aiagent_config.get_config(summary["id"])["model"]["model"], "other-model")
        # 默认配置不受影响。
        self.assertEqual(
            aiagent_config.get_config()["model"]["model"],
            aiagent_config.DEFAULT_CONFIG["model"]["model"],
        )

    def test_save_config_writes_global_keys_once(self) -> None:
        aiagent_config.ensure_config()
        other = aiagent_config.create_profile("另一套")
        aiagent_config.save_config({"enabled": True, "quota": {"enabled": True}})

        # 全局段对所有配置文件都可见。
        self.assertTrue(aiagent_config.get_config()["enabled"])
        self.assertTrue(aiagent_config.get_config(other["id"])["quota"]["enabled"])
        doc = self.read_raw()
        self.assertNotIn("enabled", doc["profiles"]["default"])

    def test_save_config_persists_chatlog_as_global_key(self) -> None:
        aiagent_config.ensure_config()
        other = aiagent_config.create_profile("另一套")

        saved = aiagent_config.save_config(
            {"chatlog": {"enabled": False, "groups": ["123456"], "retention_days": 30}},
            other["id"],
        )
        self.assertFalse(saved["chatlog"]["enabled"])
        self.assertEqual(saved["chatlog"]["groups"], ["123456"])
        self.assertEqual(saved["chatlog"]["retention_days"], 30)
        # 未显式给出的字段补默认值
        self.assertEqual(saved["chatlog"]["max_total_mb"], 200)

        # 全局段：写进文档顶层，不落到任何配置文件里
        doc = self.read_raw()
        self.assertEqual(doc["chatlog"]["groups"], ["123456"])
        self.assertNotIn("chatlog", doc["profiles"]["default"])
        self.assertNotIn("chatlog", doc["profiles"][other["id"]])

        # 所有配置文件都看得到同一份 chatlog
        default_cfg = aiagent_config.get_config()
        self.assertFalse(default_cfg["chatlog"]["enabled"])
        self.assertEqual(default_cfg["chatlog"]["groups"], ["123456"])

        # 表单未携带 chatlog 时保留磁盘上的值，不被默认值覆盖
        kept = aiagent_config.save_config({"model": {"model": "x"}}, other["id"])
        self.assertFalse(kept["chatlog"]["enabled"])
        self.assertEqual(kept["chatlog"]["groups"], ["123456"])

    def test_unknown_profile_id_falls_back_to_active(self) -> None:
        aiagent_config.ensure_config()
        cfg = aiagent_config.get_config("no-such-profile")
        self.assertEqual(cfg["_profile_id"], "default")


class BindingResolutionTests(ProfileTestBase):
    def test_binding_scope_from_event(self) -> None:
        self.assertEqual(aiagent_config.binding_scope(make_group_event("777")), ("group", "777"))
        self.assertEqual(aiagent_config.binding_scope(make_private_event("888")), ("private", "888"))

    def test_bound_session_uses_its_profile(self) -> None:
        aiagent_config.ensure_config()
        summary = aiagent_config.create_profile("群专用")
        aiagent_config.save_config({"model": {"model": "group-model"}}, summary["id"])
        aiagent_config.set_binding("group", "111", summary["id"])

        group_cfg = aiagent_config.get_config_for_event(make_group_event("111"))
        self.assertEqual(group_cfg["_profile_id"], summary["id"])
        self.assertEqual(group_cfg["model"]["model"], "group-model")

        # 别的会话仍然走默认。
        self.assertEqual(
            aiagent_config.get_config_for_event(make_group_event("222"))["_profile_id"], "default"
        )
        self.assertEqual(
            aiagent_config.get_config_for_event(make_private_event("111"))["_profile_id"], "default"
        )

    def test_clear_binding_falls_back_to_default(self) -> None:
        aiagent_config.ensure_config()
        summary = aiagent_config.create_profile("私聊专用")
        aiagent_config.set_binding("private", "333", summary["id"])
        self.assertEqual(
            aiagent_config.get_config_for_event(make_private_event("333"))["_profile_id"],
            summary["id"],
        )

        aiagent_config.clear_binding("private", "333")
        self.assertEqual(
            aiagent_config.get_config_for_event(make_private_event("333"))["_profile_id"], "default"
        )

    def test_binding_rejects_bad_input(self) -> None:
        aiagent_config.ensure_config()
        with self.assertRaises(ValueError):
            aiagent_config.set_binding("channel", "1", "default")
        with self.assertRaises(ValueError):
            aiagent_config.set_binding("group", "", "default")
        with self.assertRaises(ValueError):
            aiagent_config.set_binding("group", "abc", "default")
        with self.assertRaises(ValueError):
            aiagent_config.set_binding("group", "111", "ghost-profile")

    def test_binding_to_deleted_profile_is_dropped_on_read(self) -> None:
        aiagent_config.ensure_config()
        # 手写一条指向不存在配置的脏绑定。
        doc = self.read_raw()
        doc["bindings"]["group"]["111"] = "ghost"
        self.write_raw(doc)

        self.assertEqual(aiagent_config.resolve_profile_id("group", "111"), "default")
        self.assertEqual(aiagent_config.get_raw_config()["bindings"]["group"], {})


class ProfileCrudTests(ProfileTestBase):
    def test_create_generates_slug_and_dedupes(self) -> None:
        aiagent_config.ensure_config()
        english = aiagent_config.create_profile("Work Profile")
        self.assertEqual(english["id"], "work-profile")

        # 纯中文名 slug 为空，回落 profile / profile-2 …
        first_cn = aiagent_config.create_profile("中文配置")
        second_cn = aiagent_config.create_profile("另一个中文配置")
        self.assertEqual(first_cn["id"], "profile")
        self.assertEqual(second_cn["id"], "profile-2")

    def test_create_rejects_duplicate_name_and_respects_cap(self) -> None:
        aiagent_config.ensure_config()
        aiagent_config.create_profile("重名")
        with self.assertRaises(ValueError):
            aiagent_config.create_profile("重名")

        # 顶到上限后继续新建应当报错。
        existing = len(aiagent_config.get_raw_config()["profiles"])
        for index in range(aiagent_config.MAX_PROFILES - existing):
            aiagent_config.create_profile(f"批量{index}")
        with self.assertRaises(ValueError):
            aiagent_config.create_profile("再来一个")

    def test_create_with_copy_from_clones_content(self) -> None:
        aiagent_config.ensure_config()
        aiagent_config.save_config({"model": {"api_key": "src-key", "model": "src-model"}})
        clone = aiagent_config.create_profile("复制品", copy_from="default")

        cfg = aiagent_config.get_config(clone["id"])
        self.assertEqual(cfg["model"]["api_key"], "src-key")
        self.assertEqual(cfg["model"]["model"], "src-model")
        self.assertEqual(cfg["_profile_name"], "复制品")

    def test_rename_updates_name_only(self) -> None:
        aiagent_config.ensure_config()
        summary = aiagent_config.create_profile("旧名字")
        renamed = aiagent_config.rename_profile(summary["id"], "新名字")
        self.assertEqual(renamed["id"], summary["id"])
        self.assertEqual(renamed["name"], "新名字")

        with self.assertRaises(ValueError):
            aiagent_config.rename_profile("ghost", "x")
        with self.assertRaises(ValueError):
            aiagent_config.rename_profile(summary["id"], "默认配置")

    def test_delete_clears_its_bindings(self) -> None:
        aiagent_config.ensure_config()
        summary = aiagent_config.create_profile("待删除")
        aiagent_config.set_binding("group", "111", summary["id"])
        aiagent_config.set_binding("private", "333", summary["id"])
        aiagent_config.set_binding("group", "222", "default")

        aiagent_config.delete_profile(summary["id"])
        bindings = aiagent_config.get_raw_config()["bindings"]
        self.assertEqual(bindings["group"], {"222": "default"})
        self.assertEqual(bindings["private"], {})

    def test_delete_rejects_last_and_active_profile(self) -> None:
        aiagent_config.ensure_config()
        with self.assertRaises(ValueError):
            aiagent_config.delete_profile("default")

        summary = aiagent_config.create_profile("第二套")
        # default 仍是全局默认，依然不能删。
        with self.assertRaises(ValueError):
            aiagent_config.delete_profile("default")

        aiagent_config.set_active_profile(summary["id"])
        aiagent_config.delete_profile("default")
        remaining = aiagent_config.get_raw_config()["profiles"]
        self.assertEqual(list(remaining), [summary["id"]])
        with self.assertRaises(ValueError):
            aiagent_config.delete_profile(summary["id"])

    def test_set_active_profile_changes_unbound_sessions(self) -> None:
        aiagent_config.ensure_config()
        summary = aiagent_config.create_profile("新默认")
        aiagent_config.set_active_profile(summary["id"])

        self.assertEqual(aiagent_config.get_config()["_profile_id"], summary["id"])
        self.assertEqual(
            aiagent_config.get_config_for_event(make_group_event("999"))["_profile_id"],
            summary["id"],
        )
        with self.assertRaises(ValueError):
            aiagent_config.set_active_profile("ghost")

    def test_list_profiles_reports_counts_and_active(self) -> None:
        aiagent_config.ensure_config()
        summary = aiagent_config.create_profile("统计用")
        aiagent_config.set_binding("group", "111", summary["id"])
        aiagent_config.set_binding("private", "333", summary["id"])

        by_id = {item["id"]: item for item in aiagent_config.list_profiles()}
        self.assertTrue(by_id["default"]["is_active"])
        self.assertEqual(by_id[summary["id"]]["bound_count"], 2)
        self.assertEqual(by_id["default"]["bound_count"], 0)
        # 摘要里只暴露「有没有 key」，不暴露 key 本身。
        self.assertNotIn("api_key", by_id["default"])
        self.assertIn("api_key_set", by_id["default"])

    def test_find_profile_id_matches_id_and_name(self) -> None:
        aiagent_config.ensure_config()
        summary = aiagent_config.create_profile("Nightly")
        self.assertEqual(aiagent_config.find_profile_id(summary["id"]), summary["id"])
        self.assertEqual(aiagent_config.find_profile_id("nightly"), summary["id"])
        self.assertEqual(aiagent_config.find_profile_id("默认配置"), "default")
        self.assertIsNone(aiagent_config.find_profile_id("不存在"))
        self.assertIsNone(aiagent_config.find_profile_id(""))


class ProfileCommandContractTests(unittest.TestCase):
    """命令注册与文案默认值：bot_messages.json 是 gitignore 的，

    老部署只能靠 DEFAULT_MESSAGES 里的默认值生效。
    """

    def test_commands_are_superuser_only(self) -> None:
        from core.command_router import iter_commands

        specs = {spec.name: spec for spec in iter_commands()}
        for name in ("AI配置列表", "切换AI配置", "解绑AI配置"):
            self.assertIn(name, specs, f"命令未注册：{name}")
            self.assertTrue(specs[name].superuser_only, f"{name} 必须限超级用户")
        self.assertIn("配置列表", specs["AI配置列表"].aliases)

    def test_message_keys_have_defaults(self) -> None:
        aiagent_messages = DEFAULT_MESSAGES["aiagent"]
        for key in (
            "profile_list_header",
            "profile_list_item",
            "profile_switch_usage",
            "profile_not_found",
            "profile_switched",
            "profile_unbound",
        ):
            self.assertIn(key, aiagent_messages, f"缺少 DEFAULT_MESSAGES 默认值：aiagent.{key}")
            self.assertTrue(str(aiagent_messages[key]).strip())

    def test_kind_labels_cover_all_binding_kinds(self) -> None:
        for kind in aiagent_config.BINDING_KINDS:
            self.assertIn(kind, profile_commands._KIND_LABELS)


if __name__ == "__main__":
    unittest.main()
