"""Activities API helper for the bot admin panel."""

from __future__ import annotations

from typing import Any

from core.activity_tracker import QUEUE_SIZES, snapshot


def activity_state() -> dict[str, Any]:
    """Return current activities and queue depths for the admin API."""
    return {
        "activities": snapshot(),
        "queues": dict(QUEUE_SIZES),
    }
