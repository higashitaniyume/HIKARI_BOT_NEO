from __future__ import annotations

import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Any

import edge_tts
import httpx
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command

from .config import get_config

logger = logging.getLogger("HikariBot.TTSSpeaker")

_last_used_at: dict[str, float] = {}
FISH_TAFFY_REFERENCE_ID = "55b28b196e1c4fff9a55cd32a46eff25"


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _safe_percent(value: Any, default: str = "+0%") -> str:
    text = str(value or default).strip()
    return text if re.fullmatch(r"[+-]\d{1,3}%", text) else default


def _safe_pitch(value: Any, default: str = "+0Hz") -> str:
    text = str(value or default).strip()
    return text if re.fullmatch(r"[+-]\d{1,4}Hz", text) else default


def _safe_timeout(value: Any, default: int, *, minimum: int = 1, maximum: int = 300) -> int:
    try:
        timeout = int(value)
    except Exception:
        return default
    return min(max(timeout, minimum), maximum)


def _safe_provider(value: Any) -> str:
    provider = str(value or "edge").strip().casefold().replace("-", "_")
    return provider if provider in {"edge", "fish", "fish_audio"} else "edge"


def _safe_fish_model(value: Any) -> str:
    model = str(value or "s2-pro").strip()
    return model if model in {"s1", "s2-pro", "s2.1-pro-free"} else "s2-pro"


def _safe_fish_format(value: Any) -> str:
    fmt = str(value or "mp3").strip().lower()
    return fmt if fmt in {"mp3", "wav", "opus"} else "mp3"


def _safe_fish_latency(value: Any) -> str:
    latency = str(value or "normal").strip().lower()
    return latency if latency in {"normal", "balanced"} else "normal"


def _safe_speed(value: Any) -> float:
    try:
        speed = float(value)
    except Exception:
        return 1.0
    return min(max(speed, 0.5), 2.0)


def _cleanup_cache(cache_dir: Path, ttl_minutes: Any) -> None:
    try:
        ttl_seconds = max(1, int(ttl_minutes)) * 60
    except Exception:
        ttl_seconds = 3600

    if not cache_dir.is_dir():
        return

    now = time.time()
    removed = 0
    for path in cache_dir.glob("*.mp3"):
        try:
            if now - path.stat().st_mtime > ttl_seconds:
                path.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    if removed:
        logger.debug("[TTS] 清理过期语音缓存 %d 个", removed)


def _cache_path(cache_dir: Path, text: str, cfg: dict[str, Any], provider: str, suffix: str = ".mp3") -> Path:
    key = "|".join([
        provider,
        text,
        str(cfg.get("voice") or ""),
        str(cfg.get("rate") or ""),
        str(cfg.get("volume") or ""),
        str(cfg.get("pitch") or ""),
        str((cfg.get("fish_audio") or {}).get("reference_id") or ""),
        str((cfg.get("fish_audio") or {}).get("model") or ""),
        str((cfg.get("fish_audio") or {}).get("format") or ""),
        str((cfg.get("fish_audio") or {}).get("latency") or ""),
        str((cfg.get("fish_audio") or {}).get("speed") or ""),
    ])
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return cache_dir / f"tts_{digest}{suffix}"


def _check_cooldown(user_id: str, cooldown_seconds: Any) -> int:
    try:
        cooldown = max(0, int(cooldown_seconds))
    except Exception:
        cooldown = 5
    if cooldown <= 0:
        return 0

    now = time.monotonic()
    last_used = _last_used_at.get(user_id, 0.0)
    remain = int(cooldown - (now - last_used))
    if remain > 0:
        return remain
    _last_used_at[user_id] = now
    return 0


async def _render_edge_tts(text: str, cfg: dict[str, Any], cache_dir: Path) -> Path:
    output_path = _cache_path(cache_dir, text, cfg, "edge", ".mp3")
    if output_path.is_file() and output_path.stat().st_size > 0:
        return output_path

    voice = str(cfg.get("voice") or "zh-CN-XiaoxiaoNeural").strip()
    communicate = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate=_safe_percent(cfg.get("rate"), "+0%"),
        volume=_safe_percent(cfg.get("volume"), "+0%"),
        pitch=_safe_pitch(cfg.get("pitch"), "+0Hz"),
        proxy=str(cfg.get("proxy") or "").strip() or None,
        connect_timeout=_safe_timeout(cfg.get("connect_timeout"), 10),
        receive_timeout=_safe_timeout(cfg.get("receive_timeout"), 60),
    )
    await communicate.save(str(output_path))

    if not output_path.is_file() or output_path.stat().st_size <= 0:
        raise RuntimeError("Edge TTS 生成结果为空。")
    return output_path


async def _render_fish_tts(text: str, cfg: dict[str, Any], cache_dir: Path) -> Path:
    fish_cfg = cfg.get("fish_audio") if isinstance(cfg.get("fish_audio"), dict) else {}
    if not fish_cfg.get("enabled", True):
        raise RuntimeError("Fish Audio TTS 当前已关闭。")

    api_key = str(fish_cfg.get("api_key") or "").strip()
    if not api_key:
        raise RuntimeError("Fish Audio API Key 未配置。")

    reference_id = str(fish_cfg.get("reference_id") or FISH_TAFFY_REFERENCE_ID).strip()
    if not reference_id:
        raise RuntimeError("Fish Audio reference_id 未配置。")

    fmt = _safe_fish_format(fish_cfg.get("format"))
    output_path = _cache_path(cache_dir, text, cfg, "fish_audio", f".{fmt}")
    if output_path.is_file() and output_path.stat().st_size > 0:
        return output_path

    timeout = httpx.Timeout(
        connect=_safe_timeout(cfg.get("connect_timeout"), 10),
        read=_safe_timeout(cfg.get("receive_timeout"), 60, maximum=600),
        write=30,
        pool=30,
    )
    payload = {
        "text": text,
        "reference_id": reference_id,
        "format": fmt,
        "latency": _safe_fish_latency(fish_cfg.get("latency")),
        "speed": _safe_speed(fish_cfg.get("speed")),
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "model": _safe_fish_model(fish_cfg.get("model")),
    }
    proxy = str(cfg.get("proxy") or "").strip() or None
    async with httpx.AsyncClient(timeout=timeout, proxy=proxy) as client:
        response = await client.post("https://api.fish.audio/v1/tts", headers=headers, json=payload)
        if response.status_code >= 400:
            detail = response.text[:300]
            raise RuntimeError(f"Fish Audio 请求失败: HTTP {response.status_code} {detail}")
        output_path.write_bytes(response.content)

    if not output_path.is_file() or output_path.stat().st_size <= 0:
        raise RuntimeError("Fish Audio 生成结果为空。")
    return output_path


async def _render_tts(text: str, cfg: dict[str, Any], provider: str | None = None) -> Path:
    cache_dir = Path(str(cfg.get("cache_dir") or "/tmp/hikari_bot/tts"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_cache(cache_dir, cfg.get("cache_ttl_minutes", 60))

    selected_provider = _safe_provider(provider or cfg.get("default_provider"))
    if selected_provider in {"fish", "fish_audio"}:
        return await _render_fish_tts(text, cfg, cache_dir)
    return await _render_edge_tts(text, cfg, cache_dir)

async def _handle_tts_command(ctx: CommandContext, *, provider: str | None = None) -> None:
    cfg = get_config()
    if not cfg.get("enabled", True):
        await ctx.send(Message(msg("tts.disabled")))
        return

    text = _normalize_text(ctx.args)
    if not text:
        await ctx.send(Message(msg("tts.usage")))
        return

    max_chars = _safe_timeout(cfg.get("max_chars"), 120, minimum=1, maximum=1000)
    if len(text) > max_chars:
        await ctx.send(Message(msg("tts.too_long", max_chars=max_chars)))
        return

    remain = _check_cooldown(ctx.event.get_user_id(), cfg.get("cooldown_seconds", 5))
    if remain > 0:
        await ctx.send(Message(msg("tts.cooldown", seconds=remain)))
        return

    try:
        output_path = await _render_tts(text, cfg, provider=provider)
        await ctx.send(Message(MessageSegment.record(output_path.resolve().as_uri())))
    except Exception as e:
        logger.exception("[TTS] 生成或发送语音失败: %s", e)
        await ctx.send(Message(msg("tts.failed")))


@command("说话", aliases=("tts", "TTS"), description="用配置的 TTS 生成语音", usage="说话 <文本>")
async def cmd_say(ctx: CommandContext) -> None:
    await _handle_tts_command(ctx)


@command("永雏塔菲", aliases=("塔菲",), description="用 Fish Audio 永雏塔菲音色说话", usage="永雏塔菲 <文本>")
async def cmd_taffy(ctx: CommandContext) -> None:
    await _handle_tts_command(ctx, provider="fish_audio")
