from __future__ import annotations

import json
import re
from typing import Any

def _json_bytes(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "启用"}


def _parse_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return min(max(parsed, minimum), maximum)


def _parse_fish_model(value: Any, default: str = "s2-pro") -> str:
    model = str(value or default).strip()
    if model in {"s1", "s2-pro", "s2.1-pro", "s2.1-pro-free"}:
        return model
    raise ValueError("Fish Audio 模型只能是 s1、s2-pro、s2.1-pro 或 s2.1-pro-free。")


def _parse_fish_backup_model(value: Any) -> str:
    model = str(value or "").strip()
    return "" if not model else _parse_fish_model(model)


def _parse_fish_format(value: Any, default: str = "mp3") -> str:
    fmt = str(value or default).strip().lower()
    if fmt in {"mp3", "wav", "opus", "pcm"}:
        return fmt
    raise ValueError("Fish Audio 输出格式只能是 mp3、wav、opus 或 pcm。")


def _parse_fish_latency(value: Any, default: str = "normal") -> str:
    latency = str(value or default).strip().lower()
    if latency in {"low", "normal", "balanced"}:
        return latency
    raise ValueError("Fish Audio 延迟模式只能是 low、normal 或 balanced。")


def _parse_float(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        return default
    return min(max(parsed, minimum), maximum)


def _parse_str(value: Any, default: str = "", *, max_length: int = 4000) -> str:
    text = str(value if value is not None else default).strip()
    return text[:max_length]


def _parse_sample_rate(value: Any) -> int | None:
    if value in (None, "", 0, "0", "auto"):
        return None
    return _parse_int(value, 44100, minimum=8000, maximum=192000)


def _parse_mp3_bitrate(value: Any) -> int:
    bitrate = _parse_int(value, 128, minimum=64, maximum=192)
    if bitrate not in {64, 128, 192}:
        raise ValueError("MP3 比特率只能是 64、128 或 192 kbps。")
    return bitrate


def _parse_tts_voices(value: Any, fallback: list[dict[str, str]]) -> list[dict[str, str]]:
    raw_voices = value if isinstance(value, list) else fallback
    voices: list[dict[str, str]] = []
    names: set[str] = set()
    for item in raw_voices:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        reference_id = str(item.get("reference_id") or "").strip()
        if not name or len(name) > 40 or not re.fullmatch(r"[A-Za-z0-9_-]{4,128}", reference_id):
            raise ValueError("音色名称或模型 ID 格式无效。")
        normalized_name = name.casefold()
        if normalized_name in names:
            raise ValueError(f"音色名称重复：{name}")
        names.add(normalized_name)
        voices.append({"name": name, "reference_id": reference_id})
    if not voices:
        raise ValueError("至少保留一个音色。")
    return voices

