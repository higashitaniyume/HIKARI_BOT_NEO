from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import httpx
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command

from .config import get_config, save_config

logger = logging.getLogger("HikariBot.TTSSpeaker")

_last_used_at: dict[str, float] = {}


class FishAudioRequestError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"Fish Audio 请求失败: HTTP {status_code} {detail}")
        self.status_code = status_code


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _safe_timeout(value: Any, default: int, *, minimum: int = 1, maximum: int = 300) -> int:
    try:
        timeout = int(value)
    except Exception:
        return default
    return min(max(timeout, minimum), maximum)


def _safe_float(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        return default
    return min(max(parsed, minimum), maximum)


def _safe_fish_model(value: Any) -> str:
    model = str(value or "s2-pro").strip()
    return model if model in {"s1", "s2-pro", "s2.1-pro", "s2.1-pro-free"} else "s2-pro"


def _safe_fish_format(value: Any) -> str:
    fmt = str(value or "mp3").strip().lower()
    return fmt if fmt in {"mp3", "wav", "opus", "pcm"} else "mp3"


def _safe_fish_latency(value: Any) -> str:
    latency = str(value or "normal").strip().lower()
    return latency if latency in {"low", "normal", "balanced"} else "normal"


def _safe_mp3_bitrate(value: Any) -> int:
    bitrate = _safe_timeout(value, 128, minimum=64, maximum=192)
    return bitrate if bitrate in {64, 128, 192} else 128


def _safe_retry_count(value: Any) -> int:
    return _safe_timeout(value, 3, minimum=0, maximum=5)


def _safe_retry_delay(value: Any) -> float:
    return _safe_float(value, 1.0, minimum=0.1, maximum=30.0)


def _safe_sample_rate(value: Any) -> int | None:
    if value in (None, "", 0, "0", "auto"):
        return None
    return _safe_timeout(value, 44100, minimum=8000, maximum=192000)


def _cleanup_cache(cache_dir: Path, ttl_minutes: Any) -> None:
    try:
        ttl_seconds = max(1, int(ttl_minutes)) * 60
    except Exception:
        ttl_seconds = 3600
    if not cache_dir.is_dir():
        return

    now = time.time()
    for path in cache_dir.glob("tts_*"):
        try:
            if path.is_file() and now - path.stat().st_mtime > ttl_seconds:
                path.unlink(missing_ok=True)
        except OSError:
            continue


def _selected_voice(cfg: dict[str, Any]) -> dict[str, str]:
    voices = cfg.get("voices") if isinstance(cfg.get("voices"), list) else []
    selected_name = str(cfg.get("selected_voice") or "").strip()
    for voice in voices:
        if not isinstance(voice, dict):
            continue
        name = str(voice.get("name") or "").strip()
        reference_id = str(voice.get("reference_id") or "").strip()
        if name == selected_name and reference_id:
            return {"name": name, "reference_id": reference_id}
    raise RuntimeError("当前 Fish Audio 音色不存在，请在 Bot 后台重新选择音色。")


def _cache_path(cache_dir: Path, text: str, cfg: dict[str, Any], voice: dict[str, str], suffix: str) -> Path:
    fish_cfg = cfg.get("fish_audio") if isinstance(cfg.get("fish_audio"), dict) else {}
    cache_cfg = {key: value for key, value in fish_cfg.items() if key != "api_key"}
    key = "|".join([text, voice["reference_id"], json.dumps(cache_cfg, ensure_ascii=False, sort_keys=True)])
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
    remain = int(cooldown - (now - _last_used_at.get(user_id, 0.0)))
    if remain > 0:
        return remain
    _last_used_at[user_id] = now
    return 0


async def _apply_pitch(input_path: Path, output_path: Path, pitch_semitones: float, sample_rate: int) -> None:
    factor = 2 ** (pitch_semitones / 12)
    filters = f"asetrate={sample_rate}*{factor:.8f},aresample={sample_rate},atempo={1 / factor:.8f}"
    temp_path = output_path.with_name(f"{output_path.stem}.processing{output_path.suffix}")
    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(input_path), "-vn", "-af", filters, str(temp_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0 or not temp_path.is_file() or temp_path.stat().st_size <= 0:
            detail = stderr.decode("utf-8", errors="replace").strip()[:300]
            raise RuntimeError(f"音高后处理失败: {detail or 'ffmpeg 未生成有效音频'}")
        temp_path.replace(output_path)
    finally:
        temp_path.unlink(missing_ok=True)


def _should_retry(error: Exception) -> bool:
    if isinstance(error, FishAudioRequestError):
        return error.status_code == 429 or error.status_code >= 500
    return isinstance(error, httpx.HTTPError)


async def _request_fish_audio(
    client: httpx.AsyncClient,
    payload: dict[str, Any],
    headers: dict[str, str],
    *,
    model: str,
    retry_count: int,
    retry_delay_seconds: float,
) -> bytes:
    request_headers = {**headers, "model": model}
    last_error: Exception | None = None
    for attempt in range(retry_count + 1):
        try:
            response = await client.post("https://api.fish.audio/v1/tts", headers=request_headers, json=payload)
            if response.status_code >= 400:
                raise FishAudioRequestError(response.status_code, response.text[:300])
            if not response.content:
                raise RuntimeError("Fish Audio 生成结果为空。")
            return response.content
        except asyncio.CancelledError:
            raise
        except Exception as error:
            last_error = error
            if attempt >= retry_count or not _should_retry(error):
                break
            logger.warning(
                "[TTS] Fish Audio 模型 %s 第 %d 次请求失败，将在 %.1f 秒后重试: %s",
                model,
                attempt + 1,
                retry_delay_seconds,
                error,
            )
            await asyncio.sleep(retry_delay_seconds)
    assert last_error is not None
    raise last_error


async def _render_fish_tts(text: str, cfg: dict[str, Any], cache_dir: Path) -> Path:
    fish_cfg = cfg.get("fish_audio") if isinstance(cfg.get("fish_audio"), dict) else {}
    api_key = str(fish_cfg.get("api_key") or "").strip()
    if not api_key:
        raise RuntimeError("Fish Audio API Key 未配置。")

    voice = _selected_voice(cfg)
    fmt = _safe_fish_format(fish_cfg.get("format"))
    output_path = _cache_path(cache_dir, text, cfg, voice, f".{fmt}")
    if output_path.is_file() and output_path.stat().st_size > 0:
        return output_path

    sample_rate = _safe_sample_rate(fish_cfg.get("sample_rate"))
    payload: dict[str, Any] = {
        "text": text,
        "reference_id": voice["reference_id"],
        "format": fmt,
        "latency": _safe_fish_latency(fish_cfg.get("latency")),
        "temperature": _safe_float(fish_cfg.get("temperature"), 0.7, minimum=0.0, maximum=1.0),
        "top_p": _safe_float(fish_cfg.get("top_p"), 0.7, minimum=0.0, maximum=1.0),
        "chunk_length": _safe_timeout(fish_cfg.get("chunk_length"), 300, minimum=100, maximum=300),
        "normalize": bool(fish_cfg.get("normalize", True)),
        "mp3_bitrate": _safe_mp3_bitrate(fish_cfg.get("mp3_bitrate")),
        "repetition_penalty": _safe_float(fish_cfg.get("repetition_penalty"), 1.2, minimum=0.0, maximum=3.0),
        "condition_on_previous_chunks": bool(fish_cfg.get("condition_on_previous_chunks", True)),
        "prosody": {
            "speed": _safe_float(fish_cfg.get("speed"), 1.0, minimum=0.5, maximum=2.0),
            "volume": _safe_float(fish_cfg.get("volume"), 0.0, minimum=-24.0, maximum=24.0),
            "normalize_loudness": bool(fish_cfg.get("normalize_loudness", True)),
        },
    }
    if sample_rate is not None:
        payload["sample_rate"] = sample_rate

    timeout = httpx.Timeout(
        connect=_safe_timeout(cfg.get("connect_timeout"), 10),
        read=_safe_timeout(cfg.get("receive_timeout"), 60, maximum=600),
        write=30,
        pool=30,
    )
    primary_model = _safe_fish_model(fish_cfg.get("model"))
    backup_model_raw = str(fish_cfg.get("backup_model") or "").strip()
    backup_model = _safe_fish_model(backup_model_raw) if backup_model_raw else ""
    retry_count = _safe_retry_count(fish_cfg.get("retry_count"))
    retry_delay_seconds = _safe_retry_delay(fish_cfg.get("retry_delay_seconds"))
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    raw_path = output_path.with_name(f"{output_path.stem}.source{output_path.suffix}")
    proxy = str(cfg.get("proxy") or "").strip() or None
    async with httpx.AsyncClient(timeout=timeout, proxy=proxy) as client:
        try:
            content = await _request_fish_audio(
                client,
                payload,
                headers,
                model=primary_model,
                retry_count=retry_count,
                retry_delay_seconds=retry_delay_seconds,
            )
        except Exception as primary_error:
            if not backup_model or backup_model == primary_model:
                raise
            logger.warning("[TTS] 主模型 %s 失败，改用备用模型 %s: %s", primary_model, backup_model, primary_error)
            content = await _request_fish_audio(
                client,
                payload,
                headers,
                model=backup_model,
                retry_count=0,
                retry_delay_seconds=retry_delay_seconds,
            )
    raw_path.write_bytes(content)
    if not raw_path.is_file() or raw_path.stat().st_size <= 0:
        raise RuntimeError("Fish Audio 生成结果为空。")

    pitch = _safe_float(fish_cfg.get("pitch_semitones"), 0.0, minimum=-12.0, maximum=12.0)
    if pitch:
        processing_rate = sample_rate or (48000 if fmt == "opus" else 44100)
        try:
            await _apply_pitch(raw_path, output_path, pitch, processing_rate)
        finally:
            raw_path.unlink(missing_ok=True)
    else:
        raw_path.replace(output_path)
    return output_path


async def _handle_tts_command(ctx: CommandContext) -> None:
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
        cache_dir = Path(str(cfg.get("cache_dir") or "/tmp/hikari_bot/tts"))
        cache_dir.mkdir(parents=True, exist_ok=True)
        _cleanup_cache(cache_dir, cfg.get("cache_ttl_minutes", 60))
        output_path = await _render_fish_tts(text, cfg, cache_dir)
        await ctx.send(Message(MessageSegment.record(output_path.resolve().as_uri())))
    except FishAudioRequestError as e:
        logger.warning("[TTS] Fish Audio 请求失败: %s", e)
        if e.status_code == 402:
            await ctx.send(Message(msg("tts.fish_credit_insufficient")))
        elif e.status_code in {401, 403}:
            await ctx.send(Message(msg("tts.fish_auth_failed")))
        else:
            await ctx.send(Message(msg("tts.failed")))
    except Exception as e:
        logger.exception("[TTS] 生成或发送语音失败: %s", e)
        await ctx.send(Message(msg("tts.failed")))


@command("说话", aliases=("tts", "TTS","说"), description="使用当前 Fish Audio 音色生成语音", usage="说话 <文本>")
async def cmd_say(ctx: CommandContext) -> None:
    await _handle_tts_command(ctx)


@command("音色列表", aliases=("tts音色",), description="显示可用 Fish Audio 音色", usage="音色列表")
async def cmd_voice_list(ctx: CommandContext) -> None:
    cfg = get_config()
    voices = cfg.get("voices") if isinstance(cfg.get("voices"), list) else []
    names = [str(item.get("name") or "").strip() for item in voices if isinstance(item, dict)]
    await ctx.send(Message(msg("tts.voice_list", voices="、".join(name for name in names if name), current=cfg.get("selected_voice") or "未选择")))


@command("切换音色", aliases=("换音色",), description="切换当前 Fish Audio 音色", usage="切换音色 <名称>")
async def cmd_switch_voice(ctx: CommandContext) -> None:
    target = _normalize_text(ctx.args)
    if not target:
        await ctx.send(Message(msg("tts.switch_usage")))
        return
    cfg = get_config()
    voices = cfg.get("voices") if isinstance(cfg.get("voices"), list) else []
    matched = next((item for item in voices if isinstance(item, dict) and str(item.get("name") or "").strip().casefold() == target.casefold()), None)
    if not matched:
        await ctx.send(Message(msg("tts.voice_not_found", voice=target)))
        return
    selected_name = str(matched.get("name") or "").strip()
    cfg["selected_voice"] = selected_name
    save_config(cfg)
    await ctx.send(Message(msg("tts.switch_success", voice=selected_name)))
