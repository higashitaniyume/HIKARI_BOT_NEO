from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

STATE_PATH = Path("UserData/steam_deals_state.json")
_lock = threading.RLock()


def target_key(kind: str, target_id: int | str) -> str:
    return f"{kind}:{target_id}"


def was_sent_today(kind: str, target_id: int | str, date_key: str) -> bool:
    data = _read_state()
    return (data.get("sent") or {}).get(target_key(kind, target_id)) == date_key


def mark_sent(kind: str, target_id: int | str, date_key: str) -> None:
    with _lock:
        data = _read_state_unlocked()
        sent = data.setdefault("sent", {})
        if not isinstance(sent, dict):
            sent = {}
            data["sent"] = sent
        sent[target_key(kind, target_id)] = date_key
        _write_state_unlocked(data)


def _read_state() -> dict[str, Any]:
    with _lock:
        return _read_state_unlocked()


def _read_state_unlocked() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"sent": {}}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"sent": {}}
    return data if isinstance(data, dict) else {"sent": {}}


def _write_state_unlocked(data: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = STATE_PATH.with_name(f"{STATE_PATH.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, STATE_PATH)
