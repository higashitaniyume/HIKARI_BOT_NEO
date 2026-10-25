"""空间评论自动回复：定时检查自己说说下的新评论 / 未回复评论并回复。

默认关闭。首次开启（状态文件里没有基线）只做基线：把当前未回复的评论
全部记为已见、不发送任何回复，避免打开开关的瞬间把历史评论全补回一遍。
之后每隔 `interval_minutes` 检查最近 `posts` 条说说，只回复：
不是自己发的、自己还没回复过的、没在已见列表里的评论。
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

import nonebot

from . import service
from .config import get_config
from .schedule import _clean_generated

logger = logging.getLogger("HikariBot.QZone.CommentReply")

_STARTUP_DELAY_SECONDS = 45
_MAX_SEEN = 1000
_REPLY_GAP_SECONDS = 3

_task: asyncio.Task[None] | None = None
_tick_lock = asyncio.Lock()


# ── 状态 ──────────────────────────────────────────────────


def _state_path(section: dict[str, Any]) -> Path:
    return Path(str(section.get("state_path") or "UserData/qzone_comment_state.json"))


def load_state(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"baseline_done": False, "seen": {}, "daily": {}}
    except OSError as exc:
        logger.warning("[QZone] 读取评论状态失败 %s: %s", path, exc)
        return {"baseline_done": False, "seen": {}, "daily": {}}
    try:
        data = json.loads(raw)
    except ValueError:
        logger.warning("[QZone] 评论状态文件不是合法 JSON，已忽略: %s", path)
        return {"baseline_done": False, "seen": {}, "daily": {}}
    if not isinstance(data, dict):
        return {"baseline_done": False, "seen": {}, "daily": {}}
    return {
        "baseline_done": bool(data.get("baseline_done")),
        "seen": data.get("seen") if isinstance(data.get("seen"), dict) else {},
        "daily": data.get("daily") if isinstance(data.get("daily"), dict) else {},
        "last_run": str(data.get("last_run") or ""),
        "last_result": data.get("last_result") if isinstance(data.get("last_result"), dict) else {},
    }


def save_state(state: dict[str, Any], path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        logger.warning("[QZone] 写入评论状态失败 %s: %s", path, exc)


def _seen_key(post_tid: str, comment: service.CommentItem) -> str:
    return f"{post_tid}:{comment.comment_id}:{comment.uin}"


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _trim_seen(state: dict[str, Any]) -> None:
    seen = state.get("seen")
    if not isinstance(seen, dict) or len(seen) <= _MAX_SEEN:
        return
    for key in list(seen)[: len(seen) - _MAX_SEEN]:
        seen.pop(key, None)


# ── 检查与回复 ────────────────────────────────────────────


async def check_once(*, force: bool = False) -> dict[str, Any]:
    """检查一轮评论；返回本次统计。`force=True` 时即使总开关关闭也会跑一次。"""
    async with _tick_lock:
        cfg = get_config()
        section = cfg["comment_reply"]
        if not section["enabled"] and not force:
            return {"skipped": "disabled"}

        bot = _pick_bot()
        if bot is None:
            raise RuntimeError("当前没有在线的 OneBot 连接")

        self_id = str(bot.self_id)
        path = _state_path(section)
        state = load_state(path)
        posts = await service.fetch_moods(bot, self_id, section["posts"], timeout=cfg["timeout"])

        baseline = (not state["baseline_done"]) and section["baseline_on_first_run"]
        pending: list[tuple[service.MoodItem, service.CommentThread]] = []
        marked = 0

        for post in posts:
            try:
                threads = await service.fetch_comment_threads(
                    bot, self_id, post.tid, num=30, timeout=cfg["timeout"]
                )
            except (service.QzoneLoginError, service.QzoneActionError) as exc:
                logger.warning("[QZone] 读取评论失败 tid=%s: %s", post.tid, exc)
                continue
            for thread in threads:
                comment = thread.comment
                if comment.uin == self_id:
                    continue
                key = _seen_key(post.tid, comment)
                if key in state["seen"]:
                    continue
                if thread.replied_by(self_id) or baseline:
                    state["seen"][key] = _now_text()
                    marked += 1
                    continue
                pending.append((post, thread))

        if baseline:
            state["baseline_done"] = True
            state["last_run"] = _now_text()
            state["last_result"] = {"baseline": marked, "posts": len(posts)}
            _trim_seen(state)
            save_state(state, path)
            logger.info("[QZone] 评论自动回复完成基线：记录 %d 条历史评论，未回复", marked)
            return {"baseline": marked, "posts": len(posts)}

        today = date.today().isoformat()
        used = int(state["daily"].get(today, 0) or 0)
        budget = max(0, int(section["daily_limit"]) - used)
        limit = min(int(section["per_run_limit"]), budget)

        replied = 0
        failed = 0
        for post, thread in pending[:limit]:
            text = await compose_reply(post, thread.comment, section)
            try:
                await service.reply_comment(
                    bot,
                    self_id,
                    post.tid,
                    thread.comment,
                    text,
                    timeout=cfg["timeout"],
                )
            except (service.QzoneLoginError, service.QzoneActionError, RuntimeError) as exc:
                failed += 1
                logger.warning("[QZone] 自动回复评论失败 tid=%s uin=%s: %s", post.tid, thread.comment.uin, exc)
                continue
            state["seen"][_seen_key(post.tid, thread.comment)] = _now_text()
            used += 1
            state["daily"][today] = used
            replied += 1
            logger.info(
                "[QZone] 已自动回复评论 tid=%s comment_id=%s uin=%s",
                post.tid,
                thread.comment.comment_id,
                thread.comment.uin,
            )
            await asyncio.sleep(_REPLY_GAP_SECONDS)

        state["last_run"] = _now_text()
        state["last_result"] = {
            "replied": replied,
            "pending": max(0, len(pending) - replied - failed),
            "failed": failed,
            "posts": len(posts),
        }
        _trim_seen(state)
        save_state(state, path)
        return {
            "replied": replied,
            "pending": max(0, len(pending) - replied - failed),
            "failed": failed,
            "posts": len(posts),
        }


async def compose_reply(
    post: service.MoodItem,
    comment: service.CommentItem,
    section: dict[str, Any],
) -> str:
    """生成回复正文：AI（默认）失败时退回模板文案。"""
    max_chars = int(section["ai"]["max_chars"])
    if section["mode"] == "ai":
        try:
            text = await _generate_with_ai(post, comment, section)
            if text:
                return text
        except Exception as exc:  # pragma: no cover - 上游异常不该阻断自动回复
            logger.warning("[QZone] 自动回复 AI 生成失败，改用模板: %s", exc)
    return _apply_template(section["template"], comment, max_chars=max(20, max_chars))


def _apply_template(template: str, comment: service.CommentItem, *, max_chars: int) -> str:
    text = (
        str(template or "")
        .replace("{name}", comment.nickname or comment.uin)
        .replace("{content}", comment.content[:20])
        .strip()
    )
    if not text:
        text = f"谢谢{comment.nickname}的评论喵～"
    return _clean_generated(text, max_chars) or f"谢谢{comment.nickname}的评论喵～"


async def _generate_with_ai(
    post: service.MoodItem,
    comment: service.CommentItem,
    section: dict[str, Any],
) -> str:
    from plugins.aiagent.client import post_chat_completion
    from plugins.aiagent.config import get_config as get_aiagent_config
    from plugins.aiagent.persona import load_persona_prompt

    ai_cfg = section["ai"]
    agent_cfg = get_aiagent_config()
    persona_text = ""
    try:
        persona_text = load_persona_prompt(agent_cfg)
    except Exception as exc:  # pragma: no cover
        logger.warning("[QZone] 读取 AI 人格失败，仅用写作规则: %s", exc)

    request_cfg = copy.deepcopy(agent_cfg)
    model_cfg = dict(request_cfg.get("model") or {})
    model_cfg["temperature"] = 0.9
    model_cfg["max_tokens"] = int(ai_cfg["max_tokens"])
    model_cfg["timeout_seconds"] = int(ai_cfg["timeout_seconds"])
    request_cfg["model"] = model_cfg
    request_cfg["tools"] = {"max_tool_rounds": 0}
    request_cfg["thinking"] = {"enabled": False}

    system_parts = [
        persona_text,
        str(ai_cfg.get("extra_prompt") or "").strip(),
        (
            "你在 QQ 空间回复别人给你说说的评论：只输出回复正文，不要引号、"
            "不要 @ 前缀、不要话题标签、不要 Markdown，也不要解释你在做什么。"
        ),
    ]
    user_prompt = (
        f"我的说说：{post.content[:200] or '（无正文）'}\n"
        f"{comment.nickname} 的评论：{comment.content[:200] or '（无内容）'}\n"
        f"要求：第一人称口语，{max(10, int(ai_cfg['max_chars']) // 2)}~{int(ai_cfg['max_chars'])} 字，"
        "针对评论内容回应，最多一个括号小动作。"
    )
    message = await post_chat_completion(
        request_cfg,
        [
            {"role": "system", "content": "\n\n".join(part for part in system_parts if part)},
            {"role": "user", "content": user_prompt},
        ],
        tools=[],
        caller="qzone",
    )
    return _clean_generated(str(message.get("content") or ""), int(ai_cfg["max_chars"]))


# ── 定时循环 ──────────────────────────────────────────────


def _pick_bot() -> Any | None:
    try:
        bots = nonebot.get_bots()
    except Exception:  # pragma: no cover
        return None
    return next(iter(bots.values()), None) if bots else None


async def _loop() -> None:
    await asyncio.sleep(_STARTUP_DELAY_SECONDS)
    while True:
        try:
            result = await check_once()
            if result.get("replied"):
                logger.info("[QZone] 评论自动回复：本轮回复 %d 条", result["replied"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - 兜底
            logger.warning("[QZone] 评论自动回复检查失败: %s", exc)
        interval = int(get_config()["comment_reply"]["interval_minutes"]) * 60
        await asyncio.sleep(max(60, interval))


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop(), name="HikariQZoneCommentReply")
        logger.info("[QZone] 评论自动回复循环已启动")


async def stop() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None


def status_snapshot() -> dict[str, Any]:
    cfg = get_config()
    section = cfg["comment_reply"]
    state = load_state(_state_path(section))
    today = date.today().isoformat()
    return {
        "enabled": bool(section["enabled"]),
        "interval_minutes": int(section["interval_minutes"]),
        "mode": section["mode"],
        "daily_limit": int(section["daily_limit"]),
        "replied_today": int(state["daily"].get(today, 0) or 0),
        "seen": len(state["seen"]),
        "baseline_done": bool(state["baseline_done"]),
        "last_run": str(state.get("last_run") or ""),
        "last_result": state.get("last_result") or {},
    }


try:
    _driver = nonebot.get_driver()
except ValueError:  # pragma: no cover - 单测/脚本环境
    _driver = None

if _driver is not None:

    @_driver.on_startup
    async def _start_comment_reply() -> None:
        start()

    @_driver.on_shutdown
    async def _stop_comment_reply() -> None:
        await stop()
