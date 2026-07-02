from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass
from dataclasses import replace
from typing import Any

from plugins.aiagent.client import post_chat_completion
from plugins.aiagent.config import get_config as get_aiagent_config

from .feed import NewsItem

logger = logging.getLogger("HikariBot.AiNews.Summary")


@dataclass(frozen=True, slots=True)
class AiDigestSummary:
    title: str
    bullets: list[str]
    model_label: str = ""


async def enhance_digest(
    items: list[NewsItem],
    *,
    config: dict[str, Any],
    options: dict[str, Any],
) -> tuple[list[NewsItem], AiDigestSummary | None]:
    ai_cfg = _ai_summary_config(config, options)
    if not items or not _enabled(ai_cfg):
        return items, None

    try:
        response = await _request_summary(items[: _safe_int(ai_cfg.get("max_input_items"), 8, minimum=1, maximum=30)], ai_cfg)
        translated_items = _apply_item_translations(items, response, ai_cfg)
        summary = AiDigestSummary(
            title=_clean_text(response.get("overview_title")) or "AI 摘要",
            bullets=_clean_list(response.get("overview_bullets"), max_items=_safe_int(ai_cfg.get("max_summary_bullets"), 4, minimum=1, maximum=8)),
            model_label=_model_label(),
        )
        return translated_items, summary
    except Exception as e:
        if _safe_bool(ai_cfg.get("fallback_to_original"), True):
            logger.warning("[AiNews] AI 摘要/翻译失败，已降级为原始资讯图片: %s", e)
            return items, None
        raise


def summary_enabled(config: dict[str, Any], options: dict[str, Any]) -> bool:
    return _enabled(_ai_summary_config(config, options))


async def _request_summary(items: list[NewsItem], ai_cfg: dict[str, Any]) -> dict[str, Any]:
    agent_cfg = get_aiagent_config()
    request_cfg = _build_request_config(agent_cfg, ai_cfg)
    target_language = str(ai_cfg.get("target_language") or "zh-CN").strip() or "zh-CN"
    translate = _safe_bool(ai_cfg.get("translate"), True)
    prompt_items = _format_items(items, max_chars=_safe_int(ai_cfg.get("max_input_chars"), 9000, minimum=1000, maximum=30000))

    system_prompt = (
        "你是 HIKARI BOT 的 AI 资讯编辑。你只根据用户提供的资讯条目工作，不能编造来源中没有的信息。"
        "请输出严格 JSON，不要 Markdown，不要代码块。"
    )
    user_prompt = (
        f"目标语言：{target_language}\n"
        f"是否翻译标题和摘要：{'是' if translate else '否'}\n"
        "请完成两件事：\n"
        "1. 生成一段适合放在资讯图片顶部的简短总览，包含 overview_title 和 overview_bullets。\n"
        "2. 为每条资讯输出自然、准确、简短的标题和摘要；如果启用翻译，请翻译成目标语言。\n\n"
        "JSON 格式必须是：\n"
        "{\"overview_title\":\"...\",\"overview_bullets\":[\"...\"],"
        "\"items\":[{\"index\":1,\"title\":\"...\",\"summary\":\"...\"}]}\n\n"
        "资讯条目：\n"
        f"{prompt_items}"
    )

    message = await post_chat_completion(
        request_cfg,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        tools=[],
    )
    content = str(message.get("content") or "").strip()
    return _parse_json_response(content)


def _build_request_config(agent_cfg: dict[str, Any], ai_cfg: dict[str, Any]) -> dict[str, Any]:
    request_cfg = copy.deepcopy(agent_cfg)
    model_cfg = request_cfg.get("model") if isinstance(request_cfg.get("model"), dict) else {}
    model_cfg = dict(model_cfg)
    model_cfg["temperature"] = _safe_float(ai_cfg.get("temperature"), 0.2, minimum=0.0, maximum=2.0)
    model_cfg["max_tokens"] = _safe_int(ai_cfg.get("max_tokens"), 1600, minimum=256, maximum=12000)
    model_cfg["timeout_seconds"] = _safe_int(ai_cfg.get("timeout_seconds"), model_cfg.get("timeout_seconds", 60), minimum=5, maximum=600)
    request_cfg["model"] = model_cfg
    request_cfg["tools"] = {"max_tool_rounds": 0}
    return request_cfg


def _apply_item_translations(items: list[NewsItem], response: dict[str, Any], ai_cfg: dict[str, Any]) -> list[NewsItem]:
    max_summary_chars = _safe_int(ai_cfg.get("max_item_summary_chars"), 120, minimum=30, maximum=500)
    raw_items = response.get("items") if isinstance(response.get("items"), list) else []
    mapped: dict[int, dict[str, Any]] = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        try:
            index = int(raw.get("index"))
        except Exception:
            continue
        mapped[index] = raw

    result: list[NewsItem] = []
    for index, item in enumerate(items, start=1):
        raw = mapped.get(index, {})
        title = _clean_text(raw.get("title")) or item.title
        summary = _clean_text(raw.get("summary")) or item.summary
        result.append(replace(item, title=title[:220], summary=summary[:max_summary_chars]))
    return result


def _format_items(items: list[NewsItem], *, max_chars: int) -> str:
    blocks: list[str] = []
    total = 0
    for index, item in enumerate(items, start=1):
        block = (
            f"{index}. 来源：{item.source_title} / {item.source_group}\n"
            f"标题：{item.title}\n"
            f"摘要：{item.summary or '无'}\n"
            f"链接：{item.link or '无'}"
        )
        if item.published is not None:
            block = f"{block}\n时间：{item.published.isoformat(timespec='minutes')}"
        if total + len(block) > max_chars:
            break
        blocks.append(block)
        total += len(block)
    return "\n\n".join(blocks)


def _parse_json_response(content: str) -> dict[str, Any]:
    text = content.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise RuntimeError("AI 摘要返回 JSON 不是对象。")
    return data


def _ai_summary_config(config: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    cfg = config.get("ai_summary") if isinstance(config.get("ai_summary"), dict) else {}
    result = dict(cfg)
    raw_option = options.get("ai_summary")
    if isinstance(raw_option, dict):
        result.update(raw_option)
    aliases = {
        "ai_summary": "enabled",
        "summarize": "enabled",
        "translate": "translate",
        "target_language": "target_language",
        "summary_language": "target_language",
        "max_input_items": "max_input_items",
        "max_summary_bullets": "max_summary_bullets",
        "max_item_summary_chars": "max_item_summary_chars",
    }
    for option_key, cfg_key in aliases.items():
        if option_key in options and options[option_key] is not None:
            if option_key == "ai_summary" and isinstance(options[option_key], dict):
                continue
            result[cfg_key] = options[option_key]
    return result


def _enabled(ai_cfg: dict[str, Any]) -> bool:
    return _safe_bool(ai_cfg.get("enabled"), False)


def _model_label() -> str:
    cfg = get_aiagent_config()
    model_cfg = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
    return str(model_cfg.get("model") or "").strip()


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clean_list(value: Any, *, max_items: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _clean_text(item)
        if text:
            result.append(text[:160])
        if len(result) >= max_items:
            break
    return result


def _safe_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    lowered = str(value).strip().casefold()
    if lowered in {"1", "true", "yes", "on", "启用", "开启", "是"}:
        return True
    if lowered in {"0", "false", "no", "off", "禁用", "关闭", "否"}:
        return False
    return default


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
