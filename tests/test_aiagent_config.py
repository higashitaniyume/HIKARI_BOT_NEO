from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from plugins.aiagent import config as aiagent_config


@contextmanager
def temporary_cwd(path: Path):
    old_cwd = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old_cwd)


class AIAgentPersonaConfigTests(unittest.TestCase):
    def _base_cfg(self, skill_path: str) -> dict[str, object]:
        return {
            "persona": {
                "skill_path": skill_path,
                "max_chars": 12000,
                "include_references": True,
                "reference_max_depth": 1,
                "reference_max_files": 8,
                "reference_max_chars_per_file": 8000,
                "reference_max_total_chars": 24000,
                "fallback_prompt": "fallback",
            }
        }

    def test_load_persona_prompt_includes_direct_references(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            persona = root / "BotData" / "agent_personas" / "nuwa"
            persona.mkdir(parents=True)
            (persona / "SKILL.md").write_text("# Nuwa\n\nFollow [tone](tone.md).", encoding="utf-8")
            (persona / "tone.md").write_text("Use a warm but concise tone.", encoding="utf-8")

            with temporary_cwd(root):
                prompt = aiagent_config.load_persona_prompt(self._base_cfg("BotData/agent_personas/nuwa"))

        self.assertIn("# Nuwa", prompt)
        self.assertIn("引用资源: BotData/agent_personas/nuwa/tone.md", prompt)
        self.assertIn("Use a warm but concise tone.", prompt)

    def test_load_persona_prompt_blocks_references_outside_persona_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            persona = root / "BotData" / "agent_personas" / "nuwa"
            outside = root / "BotData" / "plugin_configs"
            persona.mkdir(parents=True)
            outside.mkdir(parents=True)
            (persona / "SKILL.md").write_text("Read [secret](../../plugin_configs/secret.md).", encoding="utf-8")
            (outside / "secret.md").write_text("secret content", encoding="utf-8")

            with temporary_cwd(root):
                prompt = aiagent_config.load_persona_prompt(self._base_cfg("BotData/agent_personas/nuwa"))

        self.assertIn("Read [secret]", prompt)
        self.assertNotIn("secret content", prompt)

    def test_load_persona_prompt_respects_reference_depth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            persona = root / "BotData" / "agent_personas" / "nuwa"
            persona.mkdir(parents=True)
            (persona / "SKILL.md").write_text("See first.md", encoding="utf-8")
            (persona / "first.md").write_text("First layer. See second.md", encoding="utf-8")
            (persona / "second.md").write_text("Second layer.", encoding="utf-8")

            with temporary_cwd(root):
                depth_one = aiagent_config.load_persona_prompt(self._base_cfg("BotData/agent_personas/nuwa"))
                cfg = self._base_cfg("BotData/agent_personas/nuwa")
                persona_cfg = cfg["persona"]
                assert isinstance(persona_cfg, dict)
                persona_cfg["reference_max_depth"] = 2
                depth_two = aiagent_config.load_persona_prompt(cfg)

        self.assertIn("First layer.", depth_one)
        self.assertNotIn("Second layer.", depth_one)
        self.assertIn("First layer.", depth_two)
        self.assertIn("Second layer.", depth_two)


if __name__ == "__main__":
    unittest.main()
