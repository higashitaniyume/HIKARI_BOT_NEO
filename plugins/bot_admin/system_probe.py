from __future__ import annotations

import os
import platform
import shutil
import sys
import time
from pathlib import Path
from typing import Any

_PROCESS_STARTED_AT = time.time()
_PROC_ROOT = Path("/proc")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _parse_meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    text = _read_text(_PROC_ROOT / "meminfo")
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        parts = raw_value.strip().split()
        if not parts:
            continue
        try:
            values[key] = int(parts[0]) * 1024
        except ValueError:
            continue
    return values


def _memory_probe() -> dict[str, Any]:
    meminfo = _parse_meminfo()
    total = meminfo.get("MemTotal", 0)
    available = meminfo.get("MemAvailable", 0)
    if total <= 0:
        return {"available": None, "percent": None, "total": None, "used": None}
    used = max(total - available, 0)
    percent = round(used / total * 100, 1)
    return {
        "available": available,
        "percent": percent,
        "total": total,
        "used": used,
    }


def _disk_probe() -> dict[str, Any]:
    try:
        usage = shutil.disk_usage("/")
    except OSError:
        return {"free": None, "percent": None, "total": None, "used": None}
    total = usage.total
    free = usage.free
    used = usage.used
    percent = round(used / total * 100, 1) if total > 0 else None
    return {
        "free": free,
        "percent": percent,
        "total": total,
        "used": used,
    }


def _load_average() -> list[float] | None:
    try:
        return [round(value, 2) for value in os.getloadavg()]
    except (AttributeError, OSError):
        text = _read_text(_PROC_ROOT / "loadavg").split()
        if len(text) < 3:
            return None
        try:
            return [round(float(value), 2) for value in text[:3]]
        except ValueError:
            return None


def _read_cpu_times() -> tuple[int, int] | None:
    parts = _read_text(_PROC_ROOT / "stat").splitlines()
    if not parts or not parts[0].startswith("cpu "):
        return None
    try:
        values = [int(value) for value in parts[0].split()[1:]]
    except ValueError:
        return None
    if len(values) < 5:
        return None
    idle = values[3] + values[4]
    total = sum(values)
    return idle, total


def _cpu_percent() -> float | None:
    first = _read_cpu_times()
    if first is None:
        return None
    time.sleep(0.05)
    second = _read_cpu_times()
    if second is None:
        return None
    idle_delta = second[0] - first[0]
    total_delta = second[1] - first[1]
    if total_delta <= 0:
        return None
    busy = max(0.0, min(1.0, 1.0 - idle_delta / total_delta))
    return round(busy * 100, 1)


def _system_uptime_seconds() -> float | None:
    text = _read_text(_PROC_ROOT / "uptime").split()
    if not text:
        return None
    try:
        return round(float(text[0]), 1)
    except ValueError:
        return None


def _process_memory_bytes() -> int | None:
    status = _read_text(_PROC_ROOT / str(os.getpid()) / "status")
    for line in status.splitlines():
        if not line.startswith("VmRSS:"):
            continue
        parts = line.split()
        if len(parts) < 2:
            return None
        try:
            return int(parts[1]) * 1024
        except ValueError:
            return None
    return None


def _process_thread_count() -> int | None:
    status = _read_text(_PROC_ROOT / str(os.getpid()) / "status")
    for line in status.splitlines():
        if not line.startswith("Threads:"):
            continue
        parts = line.split()
        if len(parts) < 2:
            return None
        try:
            return int(parts[1])
        except ValueError:
            return None
    return None


def system_probe_state() -> dict[str, Any]:
    now = time.time()
    return {
        "captured_at": now,
        "host": {
            "hostname": platform.node() or "",
            "platform": platform.platform(),
            "python": sys.version.split()[0],
        },
        "cpu": {
            "count": os.cpu_count(),
            "load_average": _load_average(),
            "percent": _cpu_percent(),
        },
        "memory": _memory_probe(),
        "disk": _disk_probe(),
        "uptime_seconds": _system_uptime_seconds(),
        "process": {
            "pid": os.getpid(),
            "uptime_seconds": round(now - _PROCESS_STARTED_AT, 1),
            "rss_bytes": _process_memory_bytes(),
            "thread_count": _process_thread_count(),
        },
    }
