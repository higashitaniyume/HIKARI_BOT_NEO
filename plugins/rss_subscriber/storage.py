from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger("HikariBot.RssSubscriber.Storage")

STATE_PATH = Path("UserData/rss_subscriber_state.json")


def has_seen_state(subscription_id: str) -> bool:
    state = _read_state()
    seen = state.get("seen") if isinstance(state.get("seen"), dict) else {}
    return subscription_id in seen and bool(seen.get(subscription_id))


def unseen_keys(subscription_id: str, entry_keys: Iterable[str]) -> list[str]:
    state = _read_state()
    seen = state.get("seen") if isinstance(state.get("seen"), dict) else {}
    subscription_seen = seen.get(subscription_id) if isinstance(seen.get(subscription_id), dict) else {}
    return [key for key in entry_keys if key and key not in subscription_seen]


def mark_seen(subscription_id: str, entry_keys: Iterable[str], *, max_entries: int = 1000) -> None:
    keys = [str(key) for key in entry_keys if str(key)]
    if not keys:
        return

    state = _read_state()
    seen = state.setdefault("seen", {})
    subscription_seen = seen.setdefault(subscription_id, {})
    now = datetime.now().isoformat(timespec="seconds")
    for key in keys:
        subscription_seen[key] = now

    if max_entries > 0 and len(subscription_seen) > max_entries:
        ordered = sorted(subscription_seen.items(), key=lambda item: str(item[1]), reverse=True)
        seen[subscription_id] = dict(ordered[:max_entries])

    _write_state(state)


def _read_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"version": 1, "seen": {}}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("[RSS] 状态读取失败，将使用空状态: %s", e)
        return {"version": 1, "seen": {}}
    return data if isinstance(data, dict) else {"version": 1, "seen": {}}


def _write_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = STATE_PATH.with_suffix(f"{STATE_PATH.suffix}.tmp")
    tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(STATE_PATH)
