from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core.bot_messages as bot_messages
import core.resources as resources


class BotMessageBackfillTests(unittest.TestCase):
    def setUp(self) -> None:
        resources._json_cache.clear()

    def tearDown(self) -> None:
        resources._json_cache.clear()

    def test_get_message_backfills_missing_default_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resource_dir = Path(tmp) / "BotData" / "resources"
            resource_dir.mkdir(parents=True)
            resource_path = resource_dir / "bot_messages.json"
            resource_path.write_text(json.dumps({"osu": {}}, ensure_ascii=False), encoding="utf-8")

            with patch.object(resources, "RESOURCE_DIR", resource_dir):
                text = bot_messages.get_message("osu.help")
                saved = json.loads(resource_path.read_text(encoding="utf-8"))

        self.assertEqual(text, bot_messages.DEFAULT_MESSAGES["osu"]["help"])
        self.assertEqual(saved["osu"]["help"], bot_messages.DEFAULT_MESSAGES["osu"]["help"])


if __name__ == "__main__":
    unittest.main()
