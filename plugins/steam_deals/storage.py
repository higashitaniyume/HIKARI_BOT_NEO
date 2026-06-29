from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from datetime import datetime, timezone
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


def annotate_price_changes(
    deals: list[Any],
    *,
    enabled: bool = True,
    mark_first_seen_as_new: bool = True,
    max_entries: int = 5000,
) -> None:
    if not enabled:
        return
    with _lock:
        data = _read_state_unlocked()
        snapshot = _coerce_snapshot(data.get("price_snapshot"))
        initialized = bool(data.get("price_snapshot_initialized"))
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        for deal in deals:
            appid = _safe_int(getattr(deal, "appid", 0))
            if appid <= 0:
                continue
            key = str(appid)
            previous = snapshot.get(key)
            if _is_current_discount(deal):
                if previous:
                    if _is_discount_deeper(deal, previous):
                        getattr(deal, "categories").add("折扣加深")
                elif initialized and mark_first_seen_as_new:
                    getattr(deal, "categories").add("新打折")
            snapshot[key] = {
                "appid": appid,
                "name": str(getattr(deal, "name", "")),
                "discount_percent": _safe_int(getattr(deal, "discount_percent", 0)),
                "original_price_cents": _safe_int(getattr(deal, "original_price_cents", 0)),
                "final_price_cents": _safe_int(getattr(deal, "final_price_cents", 0)),
                "last_seen": now,
            }

        data["price_snapshot_initialized"] = True
        data["price_snapshot"] = _prune_snapshot(snapshot, max(100, max_entries))
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


def _coerce_snapshot(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for key, item in value.items():
        if isinstance(item, dict):
            result[str(key)] = item
    return result


def _prune_snapshot(snapshot: dict[str, dict[str, Any]], max_entries: int) -> dict[str, dict[str, Any]]:
    if len(snapshot) <= max_entries:
        return snapshot
    items = sorted(snapshot.items(), key=lambda item: str(item[1].get("last_seen") or ""), reverse=True)
    return dict(items[:max_entries])


def _is_current_discount(deal: Any) -> bool:
    discount = _safe_int(getattr(deal, "discount_percent", 0))
    original = _safe_int(getattr(deal, "original_price_cents", 0))
    final = _safe_int(getattr(deal, "final_price_cents", 0))
    return discount > 0 or (original > 0 and final < original)


def _is_discount_deeper(deal: Any, previous: dict[str, Any]) -> bool:
    current_discount = _safe_int(getattr(deal, "discount_percent", 0))
    previous_discount = _safe_int(previous.get("discount_percent"))
    current_final = _safe_int(getattr(deal, "final_price_cents", 0))
    previous_final = _safe_int(previous.get("final_price_cents"))
    if current_discount > previous_discount:
        return True
    return previous_final > 0 and current_final > 0 and current_final < previous_final


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
