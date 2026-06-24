from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BINDINGS_PATH = Path("UserData/osu_bindings.json")
_lock = threading.RLock()


@dataclass(slots=True)
class OsuBinding:
    qq: str
    osu_id: int
    username: str
    mode: str
    bound_at: float


def _read_all() -> dict[str, Any]:
    if not BINDINGS_PATH.exists():
        return {}
    try:
        data = json.loads(BINDINGS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_all(data: dict[str, Any]) -> None:
    BINDINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = BINDINGS_PATH.with_name(
        f"{BINDINGS_PATH.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, BINDINGS_PATH)


def get_binding(qq: str) -> OsuBinding | None:
    with _lock:
        raw = _read_all().get(str(qq))
    if not isinstance(raw, dict):
        return None
    try:
        return OsuBinding(
            qq=str(qq),
            osu_id=int(raw["osu_id"]),
            username=str(raw["username"]),
            mode=str(raw.get("mode") or "osu"),
            bound_at=float(raw.get("bound_at") or 0),
        )
    except (KeyError, TypeError, ValueError):
        return None


def set_binding(qq: str, *, osu_id: int, username: str, mode: str) -> OsuBinding:
    binding = OsuBinding(
        qq=str(qq),
        osu_id=int(osu_id),
        username=str(username),
        mode=str(mode),
        bound_at=time.time(),
    )
    with _lock:
        data = _read_all()
        data[str(qq)] = {
            "osu_id": binding.osu_id,
            "username": binding.username,
            "mode": binding.mode,
            "bound_at": binding.bound_at,
        }
        _write_all(data)
    return binding


def remove_binding(qq: str) -> bool:
    with _lock:
        data = _read_all()
        existed = str(qq) in data
        data.pop(str(qq), None)
        _write_all(data)
        return existed
