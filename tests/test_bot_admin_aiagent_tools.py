from __future__ import annotations

import copy
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core.ai_tool_registry import register_ai_tool
from plugins.aiagent.config import DEFAULT_CONFIG
from plugins.bot_admin import settings as admin_settings


@register_ai_tool(
    "unit_admin_read_tool",
    plugin_name="unit_admin",
    description="Read-only admin test tool.",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "additionalProperties": False,
    },
)
def _unit_admin_read_tool(context, arguments):
    return {"ok": True}


@register_ai_tool(
    "unit_admin_write_tool",
    plugin_name="unit_admin",
    description="Side-effect admin test tool.",
    parameters={
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
    readonly=False,
)
def _unit_admin_write_tool(context, arguments):
    return {"ok": True}


class BotAdminAIAgentToolTests(unittest.TestCase):
    def _base_config(self) -> dict[str, object]:
        return copy.deepcopy(DEFAULT_CONFIG)

    def test_tools_catalog_reports_effective_plugin_tool_state(self) -> None:
        cfg = self._base_config()
        tools = cfg["tools"]
        assert isinstance(tools, dict)
        tools["plugin_tools"] = {
            "enabled": True,
            "allow_side_effects": False,
            "enabled_names": ["unit_admin_read_tool", "unit_admin_write_tool"],
            "disabled_names": [],
        }

        catalog = {
            item["name"]: item
            for item in admin_settings._aiagent_tools_catalog(cfg)
            if item["name"].startswith("unit_admin_")
        }

        self.assertTrue(catalog["unit_admin_read_tool"]["selected"])
        self.assertTrue(catalog["unit_admin_read_tool"]["readonly"])
        self.assertFalse(catalog["unit_admin_write_tool"]["selected"])
        self.assertEqual(catalog["unit_admin_write_tool"]["blocked_reason"], "副作用工具未放行")

    def test_update_aiagent_config_saves_plugin_tool_config(self) -> None:
        current = self._base_config()
        payload = {
            "tools": {
                "plugin_tools": {
                    "enabled": False,
                    "allow_side_effects": True,
                    "enabled_names": ["unit_admin_read_tool", "unit_admin_read_tool"],
                    "disabled_names": ["mc_wiki_search"],
                }
            }
        }

        with (
            patch.object(admin_settings, "get_aiagent_config", Mock(return_value=current)),
            patch.object(admin_settings, "resolve_aiagent_persona_path", Mock(return_value=Path("BotData/agent_personas/default"))),
            patch.object(admin_settings, "save_aiagent_config", Mock(side_effect=lambda data: data)) as save_config,
        ):
            result = admin_settings._update_aiagent_config(payload)

        plugin_tools = result["tools"]["plugin_tools"]
        self.assertEqual(
            plugin_tools,
            {
                "enabled": False,
                "allow_side_effects": True,
                "enabled_names": ["unit_admin_read_tool"],
                "disabled_names": ["mc_wiki_search"],
            },
        )
        save_config.assert_called_once()

    def test_update_aiagent_config_rejects_invalid_tool_names(self) -> None:
        current = self._base_config()
        payload = {"tools": {"plugin_tools": {"enabled_names": ["../bad"]}}}

        with (
            patch.object(admin_settings, "get_aiagent_config", Mock(return_value=current)),
            patch.object(admin_settings, "resolve_aiagent_persona_path", Mock(return_value=Path("BotData/agent_personas/default"))),
        ):
            with self.assertRaises(ValueError):
                admin_settings._update_aiagent_config(payload)

    def _save_with(self, current: dict[str, object], payload: dict[str, object]) -> dict[str, object]:
        with (
            patch.object(admin_settings, "get_aiagent_config", Mock(return_value=current)),
            patch.object(admin_settings, "resolve_aiagent_persona_path", Mock(return_value=Path("BotData/agent_personas/default"))),
            patch.object(admin_settings, "save_aiagent_config", Mock(side_effect=lambda data: data)),
        ):
            return admin_settings._update_aiagent_config(payload)

    def test_update_aiagent_config_keeps_file_writes_disabled_by_default(self) -> None:
        current = self._base_config()
        result = self._save_with(current, {"tools": {"max_tool_rounds": 2}})

        self.assertFalse(result["tools"]["files"]["allow_writes"])

    def test_update_aiagent_config_round_trips_allow_writes(self) -> None:
        current = self._base_config()
        enabled = self._save_with(current, {"tools": {"files": {"allow_writes": True}}})
        self.assertTrue(enabled["tools"]["files"]["allow_writes"])

        # 后续保存（表单未携带 files 段）不应把已开启的写入静默改回关闭
        kept = self._save_with(enabled, {"tools": {"max_tool_rounds": 3}})
        self.assertTrue(kept["tools"]["files"]["allow_writes"])

    def test_update_aiagent_config_round_trips_tool_timeout(self) -> None:
        current = self._base_config()
        default = self._save_with(current, {"tools": {}})
        self.assertEqual(default["tools"]["tool_timeout_seconds"], 30.0)

        saved = self._save_with(current, {"tools": {"tool_timeout_seconds": 12.5}})
        self.assertEqual(saved["tools"]["tool_timeout_seconds"], 12.5)

        # 未携带该字段时保留当前值，而不是回到默认
        kept = self._save_with(saved, {"tools": {"max_tool_rounds": 3}})
        self.assertEqual(kept["tools"]["tool_timeout_seconds"], 12.5)

    def test_update_aiagent_config_clamps_tool_timeout(self) -> None:
        current = self._base_config()
        too_small = self._save_with(current, {"tools": {"tool_timeout_seconds": 0}})
        self.assertEqual(too_small["tools"]["tool_timeout_seconds"], 0.1)

        too_large = self._save_with(current, {"tools": {"tool_timeout_seconds": 10**6}})
        self.assertEqual(too_large["tools"]["tool_timeout_seconds"], 600.0)


if __name__ == "__main__":
    unittest.main()
