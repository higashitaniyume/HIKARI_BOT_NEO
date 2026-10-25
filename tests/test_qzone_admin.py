from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import nonebot

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="nonebot.drivers.none:Driver")

from plugins.bot_admin import qzone_admin
from plugins.qzone import service


def _fake_submit(value):
    """替身：把调用方创建的协程关掉，避免未 await 的警告。"""

    def _submit(coro, *, timeout=0):  # noqa: ANN001
        coro.close()
        return value

    return _submit


class _TempWorkspace:
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        Path("BotData/plugin_configs").mkdir(parents=True)
        Path("UserData").mkdir(parents=True)

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _config_path(self) -> Path:
        return Path("BotData/plugin_configs/qzone.json")


class SaveConfigTests(_TempWorkspace, unittest.TestCase):
    def test_schedule_jobs_are_saved(self) -> None:
        state = qzone_admin.save_qzone_config(
            {
                "enabled": True,
                "default_visible": "friends",
                "max_length": 800,
                "schedule": {
                    "enabled": True,
                    "grace_seconds": 900,
                    "ai": {"enabled": True, "max_chars": 90, "extra_prompt": "短一点"},
                    "fallback_texts": "第一条\n第二条\n\n",
                    "jobs": [
                        {
                            "id": "morning",
                            "time": "08:30",
                            "days": ["mon", "周三"],
                            "visible": "private",
                            "prompt": "早安",
                        }
                    ],
                },
            }
        )

        raw = json.loads(self._config_path().read_text(encoding="utf-8"))
        self.assertEqual(raw["default_visible"], "friends")
        self.assertEqual(raw["max_length"], 800)
        self.assertEqual(raw["schedule"]["grace_seconds"], 900)
        self.assertEqual(raw["schedule"]["ai"]["max_chars"], 90)
        self.assertEqual(raw["schedule"]["ai"]["extra_prompt"], "短一点")
        self.assertEqual(raw["schedule"]["fallback_texts"], ["第一条", "第二条"])
        self.assertEqual(raw["schedule"]["jobs"][0]["days"], [0, 2])
        self.assertEqual(raw["schedule"]["jobs"][0]["visible"], "private")

        self.assertTrue(state["config"]["schedule"]["enabled"])
        self.assertEqual(state["status"]["jobs"][0]["id"], "morning")

    def test_invalid_time_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            qzone_admin.save_qzone_config({"schedule": {"jobs": [{"id": "x", "time": "25:00"}]}})

    def test_duplicate_job_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            qzone_admin.save_qzone_config(
                {
                    "schedule": {
                        "jobs": [
                            {"id": "a", "time": "08:00"},
                            {"id": "a", "time": "09:00"},
                        ]
                    }
                }
            )

    def test_permissions_block_is_kept(self) -> None:
        qzone_admin.save_qzone_config({"enabled": True})
        raw = json.loads(self._config_path().read_text(encoding="utf-8"))
        self.assertIn("permissions", raw)
        self.assertIn("whitelist", raw["permissions"])

    def test_comment_reply_section_is_saved(self) -> None:
        state = qzone_admin.save_qzone_config(
            {
                "comment_reply": {
                    "enabled": True,
                    "interval_minutes": 30,
                    "mode": "template",
                    "template": "谢谢{name}喵",
                    "ai": {"max_chars": 40},
                }
            }
        )

        raw = json.loads(self._config_path().read_text(encoding="utf-8"))
        self.assertTrue(raw["comment_reply"]["enabled"])
        self.assertEqual(raw["comment_reply"]["interval_minutes"], 30)
        self.assertEqual(raw["comment_reply"]["mode"], "template")
        self.assertEqual(raw["comment_reply"]["template"], "谢谢{name}喵")
        self.assertEqual(raw["comment_reply"]["ai"]["max_chars"], 40)
        self.assertTrue(state["config"]["comment_reply"]["enabled"])
        self.assertIn("comment_reply_status", state)


class CheckCommentsTests(_TempWorkspace, unittest.TestCase):
    def test_check_comments_now_wraps_result(self) -> None:
        with patch.object(qzone_admin, "_submit", _fake_submit({"replied": 1})), patch(
            "plugins.qzone.comment_reply.status_snapshot", return_value={"enabled": True}
        ):
            result = qzone_admin.check_comments_now()

        self.assertEqual(result["result"], {"replied": 1})
        self.assertEqual(result["status"], {"enabled": True})


class PublishValidationTests(_TempWorkspace, unittest.TestCase):
    def test_empty_text_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            qzone_admin.publish_text({"text": "  "})

    def test_too_long_text_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            qzone_admin.publish_text({"text": "喵" * 1001})
        self.assertIn("1000", str(ctx.exception))

    def test_publish_returns_visible_label(self) -> None:
        with patch.object(qzone_admin, "_submit", _fake_submit("tid9")):
            result = qzone_admin.publish_text({"text": "早安喵", "visible": "private"})

        self.assertEqual(result["tid"], "tid9")
        self.assertEqual(result["visible"], "private")
        self.assertEqual(result["visible_label"], "仅自己")
        self.assertEqual(result["length"], 3)


class PublishAsyncTests(_TempWorkspace, unittest.IsolatedAsyncioTestCase):
    async def test_publish_async_uses_service(self) -> None:
        publish = AsyncMock(return_value={"tid": "tidX"})
        with patch("plugins.qzone.schedule._pick_bot", return_value=object()), patch.object(
            service, "publish", publish
        ):
            tid = await qzone_admin._publish_async("内容", 4, 20)

        self.assertEqual(tid, "tidX")
        self.assertEqual(publish.await_args.kwargs["visible"], 4)

    async def test_publish_async_without_bot_raises(self) -> None:
        with patch("plugins.qzone.schedule._pick_bot", return_value=None):
            with self.assertRaises(RuntimeError):
                await qzone_admin._publish_async("内容", 1, 20)


class GenerateDraftTests(_TempWorkspace, unittest.TestCase):
    def test_generate_draft_wraps_result(self) -> None:
        persona = {
            "skill_path": "BotData/agent_personas/ds",
            "chars": 120,
            "preview": "",
            "model": "deepseek-flash",
        }
        with patch.object(qzone_admin, "_submit", _fake_submit(("草稿内容", "ai"))), patch.object(
            qzone_admin, "_persona_info", return_value=persona
        ):
            result = qzone_admin.generate_draft({"prompt": "下雨天", "max_chars": 60})

        self.assertEqual(result["text"], "草稿内容")
        self.assertEqual(result["source"], "ai")
        self.assertEqual(result["persona"]["skill_path"], "BotData/agent_personas/ds")
        self.assertEqual(result["max_chars"], 60)


if __name__ == "__main__":
    unittest.main()
