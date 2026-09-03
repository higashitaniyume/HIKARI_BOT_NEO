from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any

import nonebot

# plugins.bot_admin 的导入链会走到 astrbot_compat 的 get_driver()，
# 单独跑这个文件时需要先把 NoneBot 初始化出来。
try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="nonebot.drivers.none:Driver")

from plugins.bot_admin.operations import _guard_config_state, _write_guard_config
from plugins.group_guard.config import DEFAULT_GROUP_GUARD_CONFIG
from plugins.self_review.config import DEFAULT_SELF_REVIEW_CONFIG

GUARD_VIEW = Path("plugins/bot_admin/templates/partials/views/guard.html")
FORM_RE = re.compile(r'<form[^>]*data-guard-plugin="([a-z_]+)"(.*?)</form>', re.DOTALL)
FIELD_RE = re.compile(r'data-guard-field="([^"]+)"')

DEFAULTS = {
    "group_guard": DEFAULT_GROUP_GUARD_CONFIG,
    "self_review": DEFAULT_SELF_REVIEW_CONFIG,
}


def leaf_paths(config: dict[str, Any], prefix: str = "") -> set[str]:
    paths: set[str] = set()
    for key, value in config.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            paths |= leaf_paths(value, f"{path}.")
        else:
            paths.add(path)
    return paths


def form_fields() -> dict[str, list[str]]:
    html = GUARD_VIEW.read_text(encoding="utf-8")
    return {plugin: FIELD_RE.findall(body) for plugin, body in FORM_RE.findall(html)}


class GuardViewFieldTests(unittest.TestCase):
    """web 面板的风控表单必须和插件配置结构逐项对上。"""

    def test_both_forms_present(self) -> None:
        self.assertEqual(set(form_fields()), {"group_guard", "self_review"})

    def test_fields_are_unique(self) -> None:
        for plugin, fields in form_fields().items():
            with self.subTest(plugin=plugin):
                self.assertEqual(len(fields), len(set(fields)))

    def test_every_field_maps_to_a_config_leaf(self) -> None:
        for plugin, fields in form_fields().items():
            expected = leaf_paths(DEFAULTS[plugin]) | _permission_paths()
            for path in fields:
                with self.subTest(plugin=plugin, path=path):
                    self.assertIn(path, expected)

    def test_every_config_leaf_has_a_field(self) -> None:
        fields = form_fields()
        for plugin, defaults in DEFAULTS.items():
            # permissions 是空列表/空字符串结构，单独用固定路径校验。
            leaves = {path for path in leaf_paths(defaults) if not path.startswith("permissions")}
            with self.subTest(plugin=plugin):
                self.assertEqual(leaves - set(fields[plugin]), set())

    def test_permission_fields_present(self) -> None:
        for plugin, fields in form_fields().items():
            with self.subTest(plugin=plugin):
                self.assertEqual(_permission_paths() - set(fields), set())


def _permission_paths() -> set[str]:
    return {
        f"permissions.{listname}.{dimension}"
        for listname in ("whitelist", "blacklist")
        for dimension in ("enable", "user", "group")
    }


class GuardConfigStateTests(unittest.TestCase):
    def test_state_exposes_defaults_and_model_label(self) -> None:
        state = _guard_config_state()

        self.assertEqual(set(state["plugins"]), {"group_guard", "self_review"})
        self.assertIn("review_model", state)
        for plugin in ("group_guard", "self_review"):
            entry = state["plugins"][plugin]
            self.assertTrue(entry["label"])
            self.assertTrue(entry["default_prompt"])
            self.assertIn("permissions", entry["config"])

    def test_unknown_plugin_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _write_guard_config({"plugin": "aiagent", "config": {}})

    def test_non_object_config_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _write_guard_config({"plugin": "group_guard", "config": "enabled"})


if __name__ == "__main__":
    unittest.main()
