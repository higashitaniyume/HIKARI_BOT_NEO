"""QQ 空间后台接口实现：配置读写、AI 草稿、发布。

放在 bot_admin 里是为了复用现成的 HTTP 服务与鉴权；真正的空间操作仍然走
`plugins/qzone`，人格提示词统一取 AI 配置（`BotData/plugin_configs/aiagent.json`
里 persona skill）而不是空间插件自己的字段。

注意：这里对 `plugins.qzone` 的导入全部放在函数内部，避免 bot_admin 先加载时
把 qzone 插件提前导入、打乱 NoneBot 的插件加载顺序。
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import threading
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Any

from core.access_control import DEFAULT_ACCESS_RULES
from core.config_loader import clear_plugin_config_cache

logger = logging.getLogger("HikariBot.BotAdmin.QZone")

_CONFIG_PATH = Path("BotData/plugin_configs/qzone.json")
_DEFAULT_TIMEOUT = 120.0
_VISIBLE_NAMES = ("public", "friends", "private")
_main_loop: asyncio.AbstractEventLoop | None = None


def set_main_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    global _main_loop
    _main_loop = loop


def _submit(coro: Any, *, timeout: float = _DEFAULT_TIMEOUT) -> Any:
    loop = _main_loop
    if loop is not None and loop.is_running():
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeoutError:
            future.cancel()
            raise TimeoutError("空间操作超时，请稍后重试。") from None

    # 退路：复用 astrbot_compat 在启动时记录的主循环
    try:
        from plugins.astrbot_compat.runtime import submit_coroutine
    except Exception:  # pragma: no cover - 理论上不会缺
        coro.close()
        raise RuntimeError("机器人主循环尚未就绪，请稍后重试。") from None

    logger.info("[QZone] 自身未记录主循环，改用 astrbot_compat 的 submit_coroutine")
    try:
        return submit_coroutine(coro, timeout=timeout)
    except RuntimeError as exc:
        raise RuntimeError(f"机器人主循环尚未就绪：{exc}") from None


# ── 配置文件 ──────────────────────────────────────────────


def _read_raw_config() -> dict[str, Any]:
    try:
        data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("[QZone] 读取空间配置失败，将按默认值处理: %s", exc)
        return {}
    return data if isinstance(data, dict) else {}


def _write_raw_config(data: dict[str, Any]) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _CONFIG_PATH.with_name(f"{_CONFIG_PATH.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, _CONFIG_PATH)
    clear_plugin_config_cache("qzone")


def _file_meta() -> dict[str, Any] | None:
    try:
        stat = _CONFIG_PATH.stat()
    except OSError:
        return None
    return {"name": _CONFIG_PATH.name, "size": stat.st_size, "mtime": stat.st_mtime}


# ── 状态 ──────────────────────────────────────────────────


def qzone_config_state() -> dict[str, Any]:
    from plugins.qzone import comment_reply, schedule
    from plugins.qzone.config import get_config

    cfg = get_config()
    return {
        "config": cfg,
        "status": schedule.status_snapshot(),
        "comment_reply_status": comment_reply.status_snapshot(),
        "persona": _persona_info(),
        "file": _file_meta(),
        "loop_ready": bool(_main_loop is not None and _main_loop.is_running()),
    }


def _persona_info() -> dict[str, Any]:
    try:
        from plugins.aiagent.config import get_config as get_aiagent_config
        from plugins.aiagent.persona import load_persona_prompt

        cfg = get_aiagent_config()
    except Exception as exc:  # pragma: no cover - AI 插件缺失时后台不该整页报错
        logger.warning("[QZone] 读取 AI 配置失败: %s", exc)
        return {"skill_path": "", "chars": 0, "preview": "", "model": "", "error": str(exc)[:160]}

    persona_cfg = cfg.get("persona") if isinstance(cfg.get("persona"), dict) else {}
    try:
        text = load_persona_prompt(cfg)
    except Exception as exc:  # pragma: no cover
        logger.warning("[QZone] 读取人格提示词失败: %s", exc)
        text = ""
    return {
        "skill_path": str(persona_cfg.get("skill_path") or ""),
        "chars": len(text),
        "preview": text[:200],
        "model": str((cfg.get("model") or {}).get("model") or ""),
    }


# ── 保存配置 ──────────────────────────────────────────────


def save_qzone_config(payload: dict[str, Any]) -> dict[str, Any]:
    from plugins.qzone.config import parse_job  # noqa: F401  (延迟导入，避免抢先加载 qzone 插件)

    raw = _read_raw_config()

    for key in ("enabled", "max_length", "feed_count", "moods_count", "timeout"):
        if key in payload:
            raw[key] = payload[key]
    if "default_visible" in payload:
        raw["default_visible"] = _visible_name(payload.get("default_visible"), "public")

    schedule_payload = payload.get("schedule")
    if isinstance(schedule_payload, dict):
        merged = raw.get("schedule") if isinstance(raw.get("schedule"), dict) else {}
        merged = copy.deepcopy(merged)

        for key in ("enabled", "check_interval_seconds", "grace_seconds", "state_path"):
            if key in schedule_payload:
                merged[key] = schedule_payload[key]

        ai_payload = schedule_payload.get("ai")
        if isinstance(ai_payload, dict):
            ai = merged.get("ai") if isinstance(merged.get("ai"), dict) else {}
            ai = dict(ai)
            for key in ("enabled", "temperature", "max_tokens", "timeout_seconds", "max_chars", "extra_prompt"):
                if key in ai_payload:
                    ai[key] = ai_payload[key]
            merged["ai"] = ai

        if "fallback_texts" in schedule_payload:
            texts = schedule_payload.get("fallback_texts")
            if isinstance(texts, str):
                texts = texts.splitlines()
            merged["fallback_texts"] = [
                str(item).strip() for item in (texts if isinstance(texts, list) else []) if str(item or "").strip()
            ]

        jobs_payload = schedule_payload.get("jobs")
        if jobs_payload is not None:
            merged["jobs"] = _normalize_jobs(jobs_payload)

        raw["schedule"] = merged

    comment_payload = payload.get("comment_reply")
    if isinstance(comment_payload, dict):
        merged_comment = raw.get("comment_reply") if isinstance(raw.get("comment_reply"), dict) else {}
        merged_comment = copy.deepcopy(merged_comment)
        for key in (
            "enabled",
            "interval_minutes",
            "posts",
            "per_run_limit",
            "daily_limit",
            "mode",
            "template",
            "baseline_on_first_run",
            "state_path",
        ):
            if key in comment_payload:
                merged_comment[key] = comment_payload[key]
        ai_payload = comment_payload.get("ai")
        if isinstance(ai_payload, dict):
            ai = merged_comment.get("ai") if isinstance(merged_comment.get("ai"), dict) else {}
            ai = dict(ai)
            for key in ("max_chars", "max_tokens", "timeout_seconds", "extra_prompt"):
                if key in ai_payload:
                    ai[key] = ai_payload[key]
            merged_comment["ai"] = ai
        raw["comment_reply"] = merged_comment

    raw.setdefault("permissions", copy.deepcopy(DEFAULT_ACCESS_RULES))
    _write_raw_config(raw)

    state = qzone_config_state()
    state["message"] = "空间设置已保存。"
    return state


def _normalize_jobs(value: Any) -> list[dict[str, Any]]:
    from plugins.qzone.config import parse_job

    if not isinstance(value, list):
        raise ValueError("定时任务必须是数组。")
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        job = parse_job(item)
        if job is None:
            raise ValueError(f"定时任务格式不正确：需要 id 和 HH:MM 时间（收到 {item!r}）")
        if job["id"] in seen:
            raise ValueError(f"定时任务 id 重复：{job['id']}")
        seen.add(job["id"])
        jobs.append(
            {
                "id": job["id"],
                "enabled": job["enabled"],
                "time": job["time"],
                "days": job["days"],
                "timezone": job["timezone"],
                "visible": job["visible"],
                "prompt": job["prompt"],
            }
        )
    return jobs


def _visible_name(value: Any, default: str) -> str:
    text = str(value or "").strip().casefold()
    return text if text in _VISIBLE_NAMES else default


# ── AI 草稿 / 发布 ────────────────────────────────────────


def generate_draft(payload: dict[str, Any]) -> dict[str, Any]:
    from plugins.qzone import schedule
    from plugins.qzone.config import get_config

    prompt = str(payload.get("prompt") or "").strip()[:500]
    raw_chars = payload.get("max_chars")
    cfg = get_config()
    max_chars = cfg["schedule"]["ai"]["max_chars"]
    if raw_chars is not None:
        try:
            max_chars = min(max(int(raw_chars), 10), 1000)
        except (TypeError, ValueError):
            pass

    text, source = _submit(schedule.generate_draft(prompt, max_chars=max_chars))
    return {
        "text": text,
        "source": source,
        "persona": _persona_info(),
        "max_chars": max_chars,
    }


async def _publish_async(text: str, visible_value: int, timeout: int) -> str:
    from plugins.qzone import schedule, service

    bot = schedule._pick_bot()
    if bot is None:
        raise RuntimeError("当前没有在线的 OneBot 连接，请确认 NapCat 已登录。")
    result = await service.publish(bot, text, visible=visible_value, timeout=timeout)
    return str(result.get("tid") or "")


def publish_text(payload: dict[str, Any]) -> dict[str, Any]:
    from plugins.qzone.config import VISIBLE_LABELS, VISIBLE_VALUES, get_config

    text = str(payload.get("text") or "").strip()
    if not text:
        raise ValueError("说说内容不能为空。")

    cfg = get_config()
    if len(text) > cfg["max_length"]:
        raise ValueError(f"内容超过 {cfg['max_length']} 字上限。")
    visible = _visible_name(payload.get("visible"), cfg["default_visible"])
    visible_value = VISIBLE_VALUES.get(visible, 1)

    tid = _submit(_publish_async(text, visible_value, cfg["timeout"]))
    return {
        "tid": tid,
        "visible": visible,
        "visible_label": VISIBLE_LABELS.get(visible_value, "公开"),
        "length": len(text),
    }


def check_comments_now() -> dict[str, Any]:
    """后台「立即检查」：跑一轮评论检查（首次会做基线，不发回复）。"""
    from plugins.qzone import comment_reply

    result = _submit(comment_reply.check_once(force=True), timeout=240.0)
    return {"result": result, "status": comment_reply.status_snapshot()}


try:  # 记录机器人主循环，供后台线程把协程丢回去执行
    import nonebot

    _driver = nonebot.get_driver()
except Exception:  # pragma: no cover - 单测/脚本环境无驱动
    _driver = None

if _driver is not None:

    @_driver.on_startup
    async def _capture_main_loop() -> None:
        set_main_loop(asyncio.get_running_loop())
        logger.info("[QZone] 后台已记录机器人主循环")
