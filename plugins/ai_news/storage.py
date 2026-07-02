from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("HikariBot.AiNews.Storage")

STATE_PATH = Path("UserData/ai_news_state.json")


def has_seen_state(scope: str = "default") -> bool:
    state = _load_state()
    seen = state.get("seen") if isinstance(state.get("seen"), dict) else {}
    values = seen.get(_scope(scope))
    return isinstance(values, list) and bool(values)


def unseen_keys(scope: str, keys: list[str]) -> list[str]:
    state = _load_state()
    seen = state.get("seen") if isinstance(state.get("seen"), dict) else {}
    known = set(seen.get(_scope(scope)) or [])
    return [key for key in keys if key not in known]


def mark_seen(scope: str, keys: list[str], *, max_entries: int) -> None:
    clean_keys = [str(key) for key in keys if str(key)]
    if not clean_keys:
        return

    state = _load_state()
    seen = state.setdefault("seen", {})
    if not isinstance(seen, dict):
        seen = {}
        state["seen"] = seen

    scope_key = _scope(scope)
    existing = [str(key) for key in seen.get(scope_key, []) if str(key)]
    merged = list(dict.fromkeys([*clean_keys, *existing]))
    seen[scope_key] = merged[:max(1, int(max_entries))]
    _save_state(state)


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {"seen": {}}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("[AiNews] 状态读取失败，将使用空状态: %s", e)
        return {"seen": {}}
    return data if isinstance(data, dict) else {"seen": {}}


def _save_state(data: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = STATE_PATH.with_name(f"{STATE_PATH.name}.{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, STATE_PATH)


def _scope(value: str) -> str:
    text = str(value or "default").strip()
    return text or "default"
