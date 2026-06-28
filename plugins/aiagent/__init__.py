from __future__ import annotations

import json
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


def _safe_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


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


def _search_endpoint(base_url: Any) -> str:
    base = str(base_url or "http://searxng-core:8080").strip().rstrip("/")
    if not base:
        base = "http://searxng-core:8080"
    if base.endswith("/search"):
        return base
    return f"{base}/search"


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


def _tools_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    return cfg.get("tools") if isinstance(cfg.get("tools"), dict) else {}


def _search_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    tools_cfg = _tools_cfg(cfg)
    return tools_cfg.get("search") if isinstance(tools_cfg.get("search"), dict) else {}


def _search_tool_enabled(cfg: dict[str, Any]) -> bool:
    search_cfg = _search_cfg(cfg)
    return _safe_bool(search_cfg.get("enabled"), True)


def _available_tools(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    if not _search_tool_enabled(cfg):
        return []
    return [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": (
                    "Search the web through the configured SearXNG instance. "
                    "Use it for current events, facts that may have changed, or questions requiring external sources."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query, written in the user's language when possible.",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of results to return.",
                            "minimum": 1,
                            "maximum": 10,
                        },
                        "categories": {
                            "type": "string",
                            "description": "Optional SearXNG categories, such as general, news, images, science.",
                        },
                        "language": {
                            "type": "string",
                            "description": "Optional SearXNG language code, or auto.",
                        },
                        "time_range": {
                            "type": "string",
                            "description": "Optional SearXNG time range: day, week, month, or year.",
                            "enum": ["day", "week", "month", "year"],
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        }
    ]


async def _search_web(cfg: dict[str, Any], arguments: dict[str, Any]) -> str:
    search_cfg = _search_cfg(cfg)
    query = str(arguments.get("query") or "").strip()
    if not query:
        return json.dumps({"error": "query is required"}, ensure_ascii=False)

    configured_max = _safe_int(search_cfg.get("max_results"), 5, minimum=1, maximum=10)
    max_results = _safe_int(arguments.get("max_results"), configured_max, minimum=1, maximum=10)
    categories = str(arguments.get("categories") or search_cfg.get("categories") or "general").strip()
    language = str(arguments.get("language") or search_cfg.get("language") or "auto").strip()
    time_range = str(arguments.get("time_range") or "").strip()
    safesearch = _safe_int(search_cfg.get("safesearch"), 1, minimum=0, maximum=2)

    params: dict[str, Any] = {
        "q": query,
        "format": "json",
        "safesearch": safesearch,
    }
    if categories:
        params["categories"] = categories
    if language and language.lower() != "auto":
        params["language"] = language
    if time_range:
        params["time_range"] = time_range

    timeout = httpx.Timeout(_safe_int(search_cfg.get("timeout_seconds"), 15, minimum=1, maximum=120))
    proxy = str(search_cfg.get("proxy") or "").strip() or None
    async with httpx.AsyncClient(timeout=timeout, proxy=proxy) as client:
        response = await client.get(_search_endpoint(search_cfg.get("base_url")), params=params)
    if response.status_code >= 400:
        return json.dumps(
            {"query": query, "error": f"SearXNG HTTP {response.status_code}", "detail": response.text[:300]},
            ensure_ascii=False,
        )

    data = response.json()
    raw_results = data.get("results") if isinstance(data, dict) else []
    results: list[dict[str, Any]] = []
    if isinstance(raw_results, list):
        for item in raw_results[:max_results]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            content = str(item.get("content") or item.get("snippet") or "").strip()
            if not title and not url and not content:
                continue
            results.append(
                {
                    "title": title[:200],
                    "url": url[:500],
                    "content": content[:500],
                    "engine": str(item.get("engine") or "").strip()[:80],
                }
            )

    payload = {
        "query": query,
        "answer": str(data.get("answer") or "").strip()[:1000] if isinstance(data, dict) else "",
        "results": results,
    }
    return json.dumps(payload, ensure_ascii=False)


async def _execute_tool_call(cfg: dict[str, Any], tool_call: dict[str, Any]) -> dict[str, str]:
    tool_call_id = str(tool_call.get("id") or "")
    function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
    name = str(function.get("name") or "").strip()
    raw_arguments = str(function.get("arguments") or "{}")
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError:
        arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}

    if name == "web_search" and _search_tool_enabled(cfg):
        try:
            content = await _search_web(cfg, arguments)
        except Exception as e:
            logger.warning("[AIAgent] 搜索工具调用失败: %s", e)
            content = json.dumps({"error": f"search failed: {e}"}, ensure_ascii=False)
    else:
        content = json.dumps({"error": f"unknown or disabled tool: {name}"}, ensure_ascii=False)

    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "name": name,
        "content": content,
    }


def _assistant_tool_message(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": message.get("content"),
        "tool_calls": message.get("tool_calls"),
    }


async def _post_chat_completion(cfg: dict[str, Any], messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
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
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
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
    if not isinstance(message, dict):
        raise RuntimeError("AI Agent 返回消息格式无效。")
    return message


async def _request_chat_completion(cfg: dict[str, Any], messages: list[dict[str, str]]) -> str:
    request_messages: list[dict[str, Any]] = [dict(message) for message in messages]
    tools = _available_tools(cfg)
    max_rounds = _safe_int(_tools_cfg(cfg).get("max_tool_rounds"), 2, minimum=0, maximum=5)

    for round_index in range(max_rounds + 1):
        try:
            message = await _post_chat_completion(cfg, request_messages, tools)
        except AIAgentRequestError as e:
            if tools and e.status_code in {400, 422}:
                logger.warning("[AIAgent] 当前模型接口可能不支持 tools，已降级为普通聊天: %s", e)
                tools = []
                message = await _post_chat_completion(cfg, request_messages, tools)
            else:
                raise
        tool_calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
        content = str(message.get("content") or "").strip()
        if not tool_calls:
            if not content:
                raise RuntimeError("AI Agent 回复为空。")
            return content

        if round_index >= max_rounds:
            if content:
                return content
            raise RuntimeError("AI Agent 工具调用轮数已达上限且没有最终回复。")

        request_messages.append(_assistant_tool_message(message))
        for tool_call in tool_calls:
            if isinstance(tool_call, dict):
                request_messages.append(await _execute_tool_call(cfg, tool_call))

    raise RuntimeError("AI Agent 回复为空。")


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
