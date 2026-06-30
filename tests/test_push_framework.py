from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from plugins.push_framework.registry import (
    PushContext,
    PushMessage,
    PushTarget,
    build_push_messages,
    get_push_source,
    register_push_source,
)
from plugins.push_framework import scheduler as push_scheduler
from plugins.push_framework import storage as push_storage

SHANGHAI_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


class FakeBot:
    def __init__(self) -> None:
        self.group_messages: list[tuple[int, object]] = []
        self.private_messages: list[tuple[int, object]] = []

    async def send_group_msg(self, *, group_id: int, message) -> None:
        self.group_messages.append((group_id, message))

    async def send_private_msg(self, *, user_id: int, message) -> None:
        self.private_messages.append((user_id, message))


class PushRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_source_lookup_returns_none(self) -> None:
        self.assertIsNone(get_push_source(""))

    async def test_source_result_is_normalized_to_messages(self) -> None:
        async def provider(ctx: PushContext):
            return ["hello", PushMessage("world")]

        register_push_source("unit_test_registry_source", provider, description="test")
        ctx = PushContext(
            bot=None,
            job_id="job",
            source="unit_test_registry_source",
            target=PushTarget("private", 42),
            options={},
            now=datetime(2026, 6, 30, tzinfo=SHANGHAI_TZ),
        )

        messages = await build_push_messages("unit_test_registry_source", ctx)

        self.assertEqual([str(item.message) for item in messages], ["hello", "world"])


class PushSchedulerTests(unittest.TestCase):
    def test_due_respects_time_weekday_and_late_grace(self) -> None:
        job = {
            "enabled": True,
            "trigger": "schedule",
            "time": "09:00",
            "timezone": "Asia/Shanghai",
            "days": ["二"],
            "late_grace_seconds": 3600,
        }

        due, token = push_scheduler.is_job_due(
            job,
            now=datetime(2026, 6, 30, 9, 30, tzinfo=SHANGHAI_TZ),
        )
        self.assertTrue(due)
        self.assertEqual(token, "2026-06-30@09:00")

        due, _ = push_scheduler.is_job_due(
            job,
            now=datetime(2026, 6, 30, 10, 30, 1, tzinfo=SHANGHAI_TZ),
        )
        self.assertFalse(due)

        due, _ = push_scheduler.is_job_due(
            job,
            now=datetime(2026, 7, 1, 9, 30, tzinfo=SHANGHAI_TZ),
        )
        self.assertFalse(due)

    def test_targets_are_normalized_and_deduplicated(self) -> None:
        targets = push_scheduler.normalize_targets(
            {
                "targets": {
                    "group_ids": ["100", "100", 0, "bad"],
                    "private_user_ids": [200, "201"],
                }
            }
        )

        self.assertEqual(targets, [PushTarget("group", 100), PushTarget("private", 200), PushTarget("private", 201)])

    def test_non_schedule_job_is_not_time_due(self) -> None:
        for trigger in ("startup", "shutdown", "manual"):
            with self.subTest(trigger=trigger):
                due, token = push_scheduler.is_job_due(
                    {
                        "enabled": True,
                        "trigger": trigger,
                        "time": "00:00",
                        "timezone": "Asia/Shanghai",
                    },
                    now=datetime(2026, 6, 30, 9, 30, tzinfo=SHANGHAI_TZ),
                )

                self.assertFalse(due)
                self.assertEqual(token, "")


class PushRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_job_sends_to_targets_and_marks_state(self) -> None:
        async def provider(ctx: PushContext):
            return f"push to {ctx.target.label}"

        register_push_source("unit_test_run_source", provider)
        job = {
            "id": "unit_job",
            "source": "unit_test_run_source",
            "targets": {
                "group_ids": [100],
                "private_user_ids": [200],
            },
            "source_options": {},
        }
        now = datetime(2026, 6, 30, 9, 0, tzinfo=SHANGHAI_TZ)

        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "push_state.json"
            with (
                patch.object(push_storage, "STATE_PATH", state_path),
                patch.object(push_scheduler, "get_config", Mock(return_value={"send_retry_attempts": 1})),
            ):
                bot = FakeBot()
                result = await push_scheduler.run_job(
                    bot,
                    job,
                    token="2026-06-30@09:00",
                    mark_state=True,
                    now=now,
                )

                self.assertEqual(result.sent, 2)
                self.assertEqual([group_id for group_id, _ in bot.group_messages], [100])
                self.assertEqual([user_id for user_id, _ in bot.private_messages], [200])
                self.assertTrue(push_storage.was_sent("unit_job", PushTarget("group", 100), "2026-06-30@09:00"))
                self.assertTrue(push_storage.was_sent("unit_job", PushTarget("private", 200), "2026-06-30@09:00"))

    async def test_run_event_jobs_sends_matching_lifecycle_jobs(self) -> None:
        async def provider(ctx: PushContext):
            return f"{ctx.job_id}:{ctx.target.label}"

        register_push_source("unit_test_lifecycle_source", provider)
        config = {
            "enabled": True,
            "send_retry_attempts": 1,
            "jobs": [
                {
                    "id": "startup_job",
                    "enabled": True,
                    "trigger": "startup",
                    "source": "unit_test_lifecycle_source",
                    "targets": {"group_ids": [100], "private_user_ids": []},
                },
                {
                    "id": "shutdown_job",
                    "enabled": True,
                    "trigger": "shutdown",
                    "source": "unit_test_lifecycle_source",
                    "targets": {"group_ids": [101], "private_user_ids": []},
                },
            ],
        }

        with patch.object(push_scheduler, "get_config", Mock(return_value=config)):
            bot = FakeBot()
            results = await push_scheduler.run_event_jobs(
                bot,
                "startup",
                now=datetime(2026, 6, 30, 9, 0, tzinfo=SHANGHAI_TZ),
            )

        self.assertEqual([result.job_id for result in results], ["startup_job"])
        self.assertEqual([group_id for group_id, _ in bot.group_messages], [100])


if __name__ == "__main__":
    unittest.main()
