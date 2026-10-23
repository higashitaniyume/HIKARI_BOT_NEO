"""AI Agent 语音工具（``send_voice``）。

把 TTS 能力以显式契约暴露给内置 AI Agent：

- 复用 ``说话`` 命令的 Fish Audio 合成、缓存与会话音色，不复制请求逻辑；
- 注册为副作用工具（``readonly=False``）且默认不启用
  （``enabled_by_default=False``），必须在后台「AI Agent → Tools 管理」里
  勾选并放行副作用工具后才会下发给模型；
- 群聊自动回复默认不允许发语音（``ai_tool.allow_auto_reply``），
  避免机器人在没人叫它的时候突然说话；
- 每次调用按会话冷却，避免模型连续调用把 Fish Audio 额度刷光。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from core.ai_tool_registry import AIToolContext, register_ai_tool

from . import (
    FishAudioRequestError,
    _cleanup_cache,
    _effective_voice,
    _normalize_text,
    _render_fish_tts,
    _safe_timeout,
)
from .config import get_config
from .session_voices import session_key

logger = logging.getLogger("HikariBot.TTSSpeaker.AITool")

TOOL_NAME = "send_voice"
DEFAULT_MAX_CHARS = 80
DEFAULT_COOLDOWN_SECONDS = 10

# 与命令的冷却分开记账：AI 调用和用户手动 `说话` 互不影响。
_last_used_at: dict[str, float] = {}


def _ai_tool_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    section = cfg.get("ai_tool")
    return section if isinstance(section, dict) else {}


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _cooldown_remaining(key: str, cooldown_seconds: Any) -> int:
    try:
        cooldown = max(0, int(cooldown_seconds))
    except Exception:
        cooldown = DEFAULT_COOLDOWN_SECONDS
    if cooldown <= 0:
        return 0
    now = time.monotonic()
    remain = int(cooldown - (now - _last_used_at.get(key, 0.0)))
    if remain > 0:
        return remain
    _last_used_at[key] = now
    return 0


def _available_voice_names(cfg: dict[str, Any]) -> list[str]:
    voices = cfg.get("voices") if isinstance(cfg.get("voices"), list) else []
    return [
        str(voice.get("name") or "").strip()
        for voice in voices
        if isinstance(voice, dict) and str(voice.get("name") or "").strip()
    ]


def _resolve_voice(
    cfg: dict[str, Any],
    event: Any,
    requested: Any,
) -> tuple[str, dict[str, Any] | None]:
    """解析本次语音使用的音色：显式指定优先，否则沿用会话音色/全局默认。"""
    requested_name = str(requested or "").strip()
    if requested_name:
        matched = next(
            (
                str(voice.get("name") or "").strip()
                for voice in cfg.get("voices") or []
                if isinstance(voice, dict)
                and str(voice.get("name") or "").strip().casefold() == requested_name.casefold()
            ),
            "",
        )
        if not matched:
            return "", {
                "error": f"unknown voice: {requested_name}",
                "available_voices": _available_voice_names(cfg),
            }
        return matched, None

    key = session_key(event) if event is not None else ""
    return _effective_voice(cfg, key), None


@register_ai_tool(
    TOOL_NAME,
    plugin_name="tts_speaker",
    description=(
        "Speak a short line aloud by generating a voice message with the configured Fish Audio "
        "TTS voice and sending it to the current chat. Use it when the user asks for a voice "
        "message or a spoken reply, or when a brief spoken line clearly fits the conversation. "
        "Keep the text conversational and short; never read Markdown, code, URLs, or long "
        "paragraphs aloud. The voice is sent immediately, so the final text reply should not "
        "repeat its content."
    ),
    parameters={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The short line to speak aloud, in the user's language.",
            },
            "voice": {
                "type": "string",
                "description": (
                    "Optional voice name from the TTS voice library. "
                    "Omit to use the current session voice or the global default voice."
                ),
            },
        },
        "required": ["text"],
        "additionalProperties": False,
    },
    readonly=False,
    enabled_by_default=False,
)
async def ai_send_voice(context: AIToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    cfg = get_config()
    if not _as_bool(cfg.get("enabled"), True):
        return {"error": "TTS is disabled"}

    text = _normalize_text(arguments.get("text"))
    if not text:
        return {"error": "text is required"}

    ai_cfg = _ai_tool_cfg(cfg)
    max_chars = _safe_timeout(
        ai_cfg.get("max_chars"),
        DEFAULT_MAX_CHARS,
        minimum=1,
        maximum=1000,
    )
    if len(text) > max_chars:
        return {
            "error": f"text is too long for a voice message ({len(text)} > {max_chars} characters)",
            "max_chars": max_chars,
        }

    if bool(getattr(context, "auto", False)) and not _as_bool(
        ai_cfg.get("allow_auto_reply"), False
    ):
        return {"error": "voice messages are not allowed in automatic replies"}

    bot = context.bot
    event = context.event
    if bot is None or event is None:
        return {"error": "no chat context to send the voice message"}

    voice_name, voice_error = _resolve_voice(cfg, event, arguments.get("voice"))
    if voice_error is not None:
        return voice_error

    key = session_key(event)
    remain = _cooldown_remaining(key, ai_cfg.get("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS))
    if remain > 0:
        return {
            "error": f"voice generation is cooling down ({remain}s remaining)",
            "retry_after_seconds": remain,
        }

    try:
        cache_dir = Path(str(cfg.get("cache_dir") or "/tmp/hikari_bot/tts"))
        cache_dir.mkdir(parents=True, exist_ok=True)
        _cleanup_cache(cache_dir, cfg.get("cache_ttl_minutes", 60))
        output_path = await _render_fish_tts(text, cfg, cache_dir, voice_name)
        await bot.send(event, Message(MessageSegment.record(output_path.resolve().as_uri())))
    except FishAudioRequestError as e:
        logger.warning("[TTS] AI 语音请求失败: %s", e)
        return {"error": "Fish Audio request failed", "status": e.status_code}
    except Exception as e:
        logger.exception("[TTS] AI 语音生成或发送失败: %s", e)
        return {"error": f"failed to generate or send voice: {type(e).__name__}: {e}"}

    logger.info("[TTS] AI 语音已发送 -> session=%s voice=%s chars=%d", key, voice_name, len(text))
    return {
        "ok": True,
        "sent": True,
        "voice": voice_name,
        "chars": len(text),
        "notice": (
            "The voice message has already been sent. Reply with a short text only; "
            "do not repeat the spoken content."
        ),
    }
