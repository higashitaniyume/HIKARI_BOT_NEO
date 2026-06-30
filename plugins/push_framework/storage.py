from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from .registry import PushTarget

logger = logging.getLogger("HikariBot.PushFramework.Storage")

STATE_PATH = Path("UserData/push_framework_state.json")


def was_sent(job_id: str, target: PushTarget, token: str) -> bool:
    state = _read_state()
    sent = state.get("sent") if isinstance(state.get("sent"), dict) else {}
    job_state = sent.get(job_id) if isinstance(sent.get(job_id), dict) else {}
    target_state = job_state.get(_target_key(target)) if isinstance(job_state.get(_target_key(target)), dict) else {}
    return token in target_state


def mark_sent(job_id: str, target: PushTarget, token: str, *, sent_at: datetime | None = None) -> None:
    state = _read_state()
    sent = state.setdefault("sent", {})
    job_state = sent.setdefault(job_id, {})
    target_state = job_state.setdefault(_target_key(target), {})
    target_state[token] = (sent_at or datetime.now()).isoformat()
    _write_state(state)


def _target_key(target: PushTarget) -> str:
    return f"{target.kind}:{target.target_id}"


def _read_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"version": 1, "sent": {}}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("[PushFramework] 推送状态读取失败，将使用空状态: %s", e)
        return {"version": 1, "sent": {}}
    return data if isinstance(data, dict) else {"version": 1, "sent": {}}


def _write_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = STATE_PATH.with_suffix(f"{STATE_PATH.suffix}.tmp")
    tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(STATE_PATH)
