"""TTS 音色的会话级配置。

每个会话（私聊 = 单个用户；群聊 = 整个群）独立维护音色，
持久化到 UserData/tts_session_voices.json。未设置过音色的
会话回退到插件全局配置 selected_voice（默认音色，由后台管理）。
"""

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("HikariBot.TTSSpeaker.SessionVoices")

SESSION_VOICES_PATH = Path("UserData/tts_session_voices.json")

_lock = threading.RLock()


def session_key(event: Any) -> str:
    """返回当前会话的唯一 key：群聊为 group:<id>，私聊为 user:<id>。"""
    group_id = getattr(event, "group_id", None)
    if group_id:
        return f"group:{group_id}"
    return f"user:{event.get_user_id()}"


def _read_all() -> dict[str, Any]:
    if not SESSION_VOICES_PATH.exists():
        return {}
    try:
        data = json.loads(SESSION_VOICES_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_all(data: dict[str, Any]) -> None:
    SESSION_VOICES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = SESSION_VOICES_PATH.with_name(
        f"{SESSION_VOICES_PATH.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, SESSION_VOICES_PATH)


def get_session_voice(key: str) -> str | None:
    """获取会话音色名称；未设置返回 None。"""
    with _lock:
        raw = _read_all().get(str(key))
    name = str(raw or "").strip()
    return name or None


def set_session_voice(key: str, voice_name: str) -> None:
    """记录会话音色（会覆盖该会话之前的音色）。"""
    voice_name = str(voice_name or "").strip()
    if not voice_name:
        return
    with _lock:
        data = _read_all()
        data[str(key)] = voice_name
        _write_all(data)
    logger.info("[TTS] 会话音色 → session=%s voice=%s", key, voice_name)
