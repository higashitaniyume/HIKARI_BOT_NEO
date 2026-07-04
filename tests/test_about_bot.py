from __future__ import annotations

import unittest

import plugins.about_bot  # noqa: F401
from core.command_router import iter_commands


class AboutBotTests(unittest.TestCase):
    def test_about_command_requires_tome_in_groups(self) -> None:
        spec = next(spec for spec in iter_commands() if spec.name == "关于")

        self.assertTrue(spec.require_tome)
        self.assertEqual(spec.usage, "关于")
        self.assertIn("about", spec.aliases)


if __name__ == "__main__":
    unittest.main()
