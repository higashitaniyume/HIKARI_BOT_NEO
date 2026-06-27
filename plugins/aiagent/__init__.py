from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent

from core.bot_messages import get_message as msg
from core.command_router import is_command_handled, mark_event_handled

from .config import get_config, load_persona_prompt

logger = logging.getLogger("HikariBot.AIAgent")

_histories: dict[str, list[dict[str, str]]] = {}
_last_used_at: dict[str, float] = {}
_URL_PATTERN = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"]+|\b(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s<>\"]*)?")


class AIAgentRequestError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"AI Agent 请求失败: HTTP {status_code} {detail}")
        self.status_code = status_code
        self.detail = detail


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


def _safe_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return min(max(parsed, minimum), maximum)


def _safe_float(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        return default
    return min(max(parsed, minimum), maximum)


def _session_key(event: MessageEvent) -> str:
    if isinstance(event, GroupMessageEvent):
        return f"group:{event.group_id}"
    return f"private:{event.get_user_id()}"


def _safe_id(value: Any) -> str:
    text = str(value or "").strip()
    return re.sub(r"[^A-Za-z0-9_-]", "_", text)[:80] or "unknown"


def _check_cooldown(user_id: str, cooldown_seconds: Any) -> int:
    cooldown = _safe_int(cooldown_seconds, 3, minimum=0, maximum=3600)
    if cooldown <= 0:
        return 0
    now = time.monotonic()
    remain = int(cooldown - (now - _last_used_at.get(user_id, 0.0)))
    if remain > 0:
        return remain
    _last_used_at[user_id] = now
    return 0


def _endpoint(base_url: Any) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        base = "https://api.openai.com/v1"
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _trim_history(history: list[dict[str, str]], max_messages: Any) -> list[dict[str, str]]:
    limit = _safe_int(max_messages, 10, minimum=0, maximum=40)
    if limit <= 0:
        return []
    return history[-limit:]


def _memory_root(cfg: dict[str, Any]) -> Path:
    memory_cfg = cfg.get("memory") if isinstance(cfg.get("memory"), dict) else {}
    root = Path(str(memory_cfg.get("root") or "UserData/aiagent_memory"))
    if not root.is_absolute():
        root = Path.cwd() / root
    return root


def _memory_paths(event: MessageEvent, cfg: dict[str, Any]) -> list[tuple[str, Path]]:
    memory_cfg = cfg.get("memory") if isinstance(cfg.get("memory"), dict) else {}
    if not memory_cfg.get("enabled", True):
        return []

    root = _memory_root(cfg)
    user_id = _safe_id(event.get_user_id())
    if isinstance(event, GroupMessageEvent):
        group_id = _safe_id(event.group_id)
        return [
            ("群聊共享记忆", root / "groups" / group_id / "memory.md"),
            ("群内个人记忆", root / "groups" / group_id / "users" / user_id / "memory.md"),
        ]
    return [("私聊个人记忆", root / "private" / user_id / "memory.md")]


def _read_memory_context(event: MessageEvent, cfg: dict[str, Any]) -> str:
    memory_cfg = cfg.get("memory") if isinstance(cfg.get("memory"), dict) else {}
    max_chars = _safe_int(memory_cfg.get("max_read_chars_per_file"), 8000, minimum=1000, maximum=80000)
    blocks: list[str] = []
    for label, path in _memory_paths(event, cfg):
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if not content:
            continue
        blocks.append(f"## {label}\n{content[-max_chars:]}")
    if not blocks:
        return ""
    return "以下是持久化记忆。请把它作为背景参考；不要主动复述文件内容。\n\n" + "\n\n".join(blocks)


def _trim_memory_file(path: Path, max_chars: int) -> None:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    if len(content) <= max_chars:
        return
    trimmed = content[-max_chars:].lstrip()
    path.write_text(f"# AI Agent Memory\n\n{trimmed}", encoding="utf-8")


def _append_memory(event: MessageEvent, cfg: dict[str, Any], user_text: str, assistant_text: str) -> None:
    memory_cfg = cfg.get("memory") if isinstance(cfg.get("memory"), dict) else {}
    if not memory_cfg.get("enabled", True):
        return

    max_file_chars = _safe_int(memory_cfg.get("max_file_chars"), 60000, minimum=5000, maximum=500000)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = (
        f"\n\n## {now}\n"
        f"- User({event.get_user_id()}): {user_text}\n"
        f"- Assistant: {assistant_text}\n"
    )
    for _, path in _memory_paths(event, cfg):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("# AI Agent Memory\n", encoding="utf-8")
            with path.open("a", encoding="utf-8") as f:
                f.write(entry)
            _trim_memory_file(path, max_file_chars)
        except OSError as e:
            logger.warning("[AIAgent] 写入 memory 失败: %s -> %s", path, e)


def _clear_memory(event: MessageEvent, cfg: dict[str, Any]) -> None:
    for _, path in _memory_paths(event, cfg):
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            logger.warning("[AIAgent] 清空 memory 失败: %s -> %s", path, e)


def _build_messages(cfg: dict[str, Any], event: MessageEvent, session_key: str, user_text: str) -> list[dict[str, str]]:
    chat_cfg = cfg.get("chat") if isinstance(cfg.get("chat"), dict) else {}
    system_prompt = load_persona_prompt(cfg)
    memory_context = _read_memory_context(event, cfg)
    if memory_context:
        system_prompt = f"{system_prompt}\n\n{memory_context}"
    extra = str(chat_cfg.get("system_prompt_extra") or "").strip()
    if extra:
        system_prompt = f"{system_prompt}\n\n额外要求：\n{extra}"

    history = _trim_history(_histories.get(session_key, []), chat_cfg.get("max_history_messages"))
    return [{"role": "system", "content": system_prompt}, *history, {"role": "user", "content": user_text}]


async def _request_chat_completion(cfg: dict[str, Any], messages: list[dict[str, str]]) -> str:
    model_cfg = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
    api_key = str(model_cfg.get("api_key") or "").strip()
    model = str(model_cfg.get("model") or "").strip()
    if not model:
        raise RuntimeError("AI Agent 模型名称未配置。")

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": _safe_float(model_cfg.get("temperature"), 0.7, minimum=0.0, maximum=2.0),
        "top_p": _safe_float(model_cfg.get("top_p"), 1.0, minimum=0.0, maximum=1.0),
        "max_tokens": _safe_int(model_cfg.get("max_tokens"), 1024, minimum=1, maximum=32000),
    }
    extra_body = model_cfg.get("extra_body")
    if isinstance(extra_body, dict):
        payload.update(extra_body)

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    timeout = httpx.Timeout(_safe_int(model_cfg.get("timeout_seconds"), 60, minimum=5, maximum=600))
    proxy = str(model_cfg.get("proxy") or "").strip() or None
    async with httpx.AsyncClient(timeout=timeout, proxy=proxy) as client:
        response = await client.post(_endpoint(model_cfg.get("base_url")), headers=headers, json=payload)
    if response.status_code >= 400:
        raise AIAgentRequestError(response.status_code, response.text[:400])

    data = response.json()
    choices = data.get("choices") if isinstance(data, dict) else None
    if not choices:
        raise RuntimeError("AI Agent 返回结果为空。")
    message = choices[0].get("message") if isinstance(choices[0], dict) else {}
    content = str(message.get("content") or "").strip()
    if not content:
        raise RuntimeError("AI Agent 回复为空。")
    return content


def _remember(session_key: str, user_text: str, assistant_text: str, cfg: dict[str, Any]) -> None:
    chat_cfg = cfg.get("chat") if isinstance(cfg.get("chat"), dict) else {}
    history = _histories.setdefault(session_key, [])
    history.extend([
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": assistant_text},
    ])
    _histories[session_key] = _trim_history(history, chat_cfg.get("max_history_messages"))


def _url_host(raw_url: str) -> str:
    value = raw_url.strip().rstrip(".,;!?，。！？；")
    if value.startswith("www."):
        value = f"https://{value}"
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    return (parsed.netloc or "").split("@")[-1].split(":")[0].lower()


def _is_blocked_media_link(text: str, cfg: dict[str, Any]) -> bool:
    chat_cfg = cfg.get("chat") if isinstance(cfg.get("chat"), dict) else {}
    domains = chat_cfg.get("blocked_url_domains") if isinstance(chat_cfg.get("blocked_url_domains"), list) else []
    blocked = [str(domain).strip().lower().lstrip(".") for domain in domains if str(domain).strip()]
    if not blocked:
        return False

    for match in _URL_PATTERN.finditer(text):
        host = _url_host(match.group(0))
        if any(host == domain or host.endswith(f".{domain}") for domain in blocked):
            return True
    return False


async def _handle_chat_event(bot: Bot, event: MessageEvent, text: str) -> None:
    cfg = get_config()
    if not cfg.get("enabled", False):
        return

    text = _normalize_text(text)
    if not text:
        return
    if _is_blocked_media_link(text, cfg):
        return
    if text.casefold() in {"reset", "重置", "清空上下文"}:
        _histories.pop(_session_key(event), None)
        _clear_memory(event, cfg)
        await bot.send(event, Message(msg("aiagent.reset_done")))
        mark_event_handled(event)
        return

    chat_cfg = cfg.get("chat") if isinstance(cfg.get("chat"), dict) else {}
    max_user_chars = _safe_int(chat_cfg.get("max_user_chars"), 2000, minimum=1, maximum=20000)
    if len(text) > max_user_chars:
        await bot.send(event, Message(msg("aiagent.too_long", max_chars=max_user_chars)))
        mark_event_handled(event)
        return

    remain = _check_cooldown(event.get_user_id(), chat_cfg.get("cooldown_seconds", 3))
    if remain > 0:
        await bot.send(event, Message(msg("aiagent.cooldown", seconds=remain)))
        mark_event_handled(event)
        return

    session_key = _session_key(event)
    try:
        messages = _build_messages(cfg, event, session_key, text)
        reply = await _request_chat_completion(cfg, messages)
        max_reply_chars = _safe_int(chat_cfg.get("max_reply_chars"), 3500, minimum=100, maximum=12000)
        if len(reply) > max_reply_chars:
            reply = f"{reply[:max_reply_chars].rstrip()}\n\n{msg('aiagent.reply_truncated')}"
        _remember(session_key, text, reply, cfg)
        _append_memory(event, cfg, text, reply)
        await bot.send(event, Message(reply))
        mark_event_handled(event)
    except AIAgentRequestError as e:
        logger.warning("[AIAgent] API 请求失败: %s", e)
        if e.status_code in {401, 403}:
            await bot.send(event, Message(msg("aiagent.auth_failed")))
        else:
            await bot.send(event, Message(msg("aiagent.failed")))
        mark_event_handled(event)
    except Exception as e:
        logger.exception("[AIAgent] 聊天失败: %s", e)
        await bot.send(event, Message(msg("aiagent.failed")))
        mark_event_handled(event)


def _should_auto_reply(event: MessageEvent) -> bool:
    if isinstance(event, GroupMessageEvent):
        return event.is_tome()
    return True


aiagent_auto_matcher = on_message(priority=99, block=False)


@aiagent_auto_matcher.handle()
async def _handle_auto_chat(bot: Bot, event: MessageEvent) -> None:
    if is_command_handled(event) or not _should_auto_reply(event):
        return
    await _handle_chat_event(bot, event, event.get_plaintext())
