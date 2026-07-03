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


if __name__ == "__main__":
    unittest.main()
