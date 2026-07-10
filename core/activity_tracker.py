"""
Shared activity tracker for cross-plugin visibility.

Plugins report their ongoing work (parsing, downloading, replying, …) so the
admin overview page can display a live feed of what the bot is doing right now.

Usage:

    from core.activity_tracker import ActivityScope

    with ActivityScope("pixiv_parser", "downloading", f"解析 Pixiv {pid}") as aid:
        ...  # do the work
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger("HikariBot.ActivityTracker")

_activities: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()
_STALE_SECONDS = 300  # auto-clean after 5 minutes
_MAX_ACTIVITIES = 200

# Plugins can write queue depths here; the snapshot function includes them.
QUEUE_SIZES: dict[str, int] = {}


def start_activity(
    plugin: str,
    action: str,
    label: str,
    description: str = "",
    status: str = "running",
    **extra: Any,
) -> str:
    """Register a new activity. Returns a short unique ID.

    Parameters
    ----------
    plugin : str
        Short plugin name, e.g. ``"pixiv_parser"``.
    action : str
        Action verb, e.g. ``"downloading"``, ``"parsing"``, ``"replying"``.
    label : str
        One-line human-readable label shown in the admin panel.
    description : str
        Optional longer description (URL, PID, user, …).
    status : str
        ``"running"`` (default) or ``"pending"`` (queued but not started yet).
    """
    aid = f"{plugin}_{int(time.time() * 1000)}_{id(extra)}"
    entry: dict[str, Any] = {
        "id": aid[:40],
        "plugin": plugin,
        "action": action,
        "label": label,
        "description": description,
        "status": status,
        "started_at": time.time(),
        "extra": dict(extra),
    }
    with _lock:
        _activities[aid] = entry
        _trim()
    return aid


def update_activity(aid: str, **changes: Any) -> None:
    """Update fields of a running activity (e.g. progress percentage)."""
    with _lock:
        entry = _activities.get(aid)
        if entry is not None:
            entry.update(changes)


def finish_activity(aid: str) -> None:
    """Remove a completed activity."""
    with _lock:
        _activities.pop(aid, None)


def snapshot() -> list[dict[str, Any]]:
    """Return a snapshot of all current (non-stale) activities."""
    now = time.time()
    alive: list[dict[str, Any]] = []
    with _lock:
        stale: list[str] = []
        for aid, entry in _activities.items():
            if now - entry.get("started_at", 0) > _STALE_SECONDS:
                stale.append(aid)
            else:
                alive.append(dict(entry))
        for aid in stale:
            _activities.pop(aid, None)
    return alive


def _trim() -> None:
    if len(_activities) > _MAX_ACTIVITIES:
        sorted_items = sorted(
            _activities.items(), key=lambda x: x[1].get("started_at", 0)
        )
        for aid, _ in sorted_items[: len(sorted_items) - _MAX_ACTIVITIES]:
            _activities.pop(aid, None)


class ActivityScope:
    """Context manager that starts an activity and finishes it on exit.

    Can be used in both sync and async code::

        with ActivityScope("pixiv_parser", "downloading", f"Pixiv {pid}") as aid:
            ...
    """

    def __init__(
        self,
        plugin: str,
        action: str,
        label: str,
        description: str = "",
        status: str = "running",
        **extra: Any,
    ) -> None:
        self._aid = start_activity(plugin, action, label, description, status, **extra)

    @property
    def aid(self) -> str:
        return self._aid

    def __enter__(self) -> str:
        return self._aid

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        finish_activity(self._aid)
