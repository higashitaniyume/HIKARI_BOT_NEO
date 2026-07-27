"""
osu! AI 工具函数模块。

为 AI Agent 提供 osu! 数据查询的封装工具函数和 payload 构建。
"""

from __future__ import annotations

import logging
from typing import Any

from core.ai_tool_registry import AIToolContext, register_ai_tool

from .api import (
    OsuApiClient,
    OsuApiError,
    OsuNotFoundError,
    mode_label,
    normalize_mode,
)
from .config import get_config
from .storage import get_binding

logger = logging.getLogger("HikariBot.OsuInfo.AITools")

_client: OsuApiClient | None = None
_client_key: tuple[str, str, str, str] | None = None


def _get_client() -> OsuApiClient:
    global _client, _client_key
    cfg = get_config()
    key = (
        str(cfg.get("client_id") or ""),
        str(cfg.get("client_secret") or ""),
        str(cfg.get("api_base") or ""),
        str(cfg.get("proxy") or ""),
    )
    if _client is None or _client_key != key:
        _client = OsuApiClient(cfg)
        _client_key = key
    return _client


def _default_mode() -> str:
    return normalize_mode(str(get_config().get("default_mode") or "osu"))


def _enabled() -> bool:
    return bool(get_config().get("enabled", True))


def _ai_resolve_osu_target(context: AIToolContext, arguments: dict[str, Any]) -> tuple[str, str] | None:
    mode = normalize_mode(str(arguments.get("mode") or ""), _default_mode())
    target = str(arguments.get("target") or "").strip()
    if target:
        return mode, target
    event = context.event if context is not None else None
    if event is None:
        return None
    binding = get_binding(event.get_user_id())
    if binding is None:
        return None
    return binding.mode or mode, str(binding.osu_id)


def _ai_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return min(max(parsed, minimum), maximum)


def _ai_user_payload(user: dict[str, Any]) -> dict[str, Any]:
    stats = user.get("statistics") if isinstance(user.get("statistics"), dict) else {}
    country = user.get("country") if isinstance(user.get("country"), dict) else {}
    return {
        "id": user.get("id"),
        "username": user.get("username"),
        "mode": mode_label(str(user.get("playmode") or "")),
        "country_code": user.get("country_code") or country.get("code") or "",
        "global_rank": stats.get("global_rank"),
        "country_rank": stats.get("country_rank"),
        "pp": stats.get("pp"),
        "hit_accuracy": stats.get("hit_accuracy"),
        "play_count": stats.get("play_count"),
        "ranked_score": stats.get("ranked_score"),
        "level": (stats.get("level") or {}).get("current") if isinstance(stats.get("level"), dict) else None,
        "profile_url": f"https://osu.ppy.sh/users/{user.get('id')}" if user.get("id") else "",
    }


def _ai_score_payload(score: dict[str, Any]) -> dict[str, Any]:
    beatmap = score.get("beatmap") if isinstance(score.get("beatmap"), dict) else {}
    beatmapset = score.get("beatmapset") if isinstance(score.get("beatmapset"), dict) else {}
    return {
        "rank": score.get("rank"),
        "pp": score.get("pp"),
        "accuracy": score.get("accuracy"),
        "score": score.get("score"),
        "max_combo": score.get("max_combo"),
        "mods": score.get("mods") if isinstance(score.get("mods"), list) else [],
        "ended_at": score.get("ended_at") or score.get("created_at") or "",
        "beatmap": {
            "id": beatmap.get("id"),
            "version": beatmap.get("version"),
            "difficulty_rating": beatmap.get("difficulty_rating"),
            "url": beatmap.get("url") or (f"https://osu.ppy.sh/beatmaps/{beatmap.get('id')}" if beatmap.get("id") else ""),
        },
        "beatmapset": {
            "id": beatmapset.get("id") or beatmap.get("beatmapset_id"),
            "title": beatmapset.get("title"),
            "artist": beatmapset.get("artist"),
            "creator": beatmapset.get("creator"),
        },
    }


def _ai_beatmap_payload(beatmap: dict[str, Any]) -> dict[str, Any]:
    beatmapset = beatmap.get("beatmapset") if isinstance(beatmap.get("beatmapset"), dict) else {}
    return {
        "id": beatmap.get("id"),
        "beatmapset_id": beatmap.get("beatmapset_id") or beatmapset.get("id"),
        "url": beatmap.get("url") or (f"https://osu.ppy.sh/beatmaps/{beatmap.get('id')}" if beatmap.get("id") else ""),
        "mode": beatmap.get("mode"),
        "version": beatmap.get("version"),
        "difficulty_rating": beatmap.get("difficulty_rating"),
        "total_length": beatmap.get("total_length"),
        "bpm": beatmap.get("bpm"),
        "cs": beatmap.get("cs"),
        "ar": beatmap.get("ar"),
        "accuracy": beatmap.get("accuracy"),
        "drain": beatmap.get("drain"),
        "playcount": beatmap.get("playcount"),
        "passcount": beatmap.get("passcount"),
        "beatmapset": _ai_beatmapset_payload(beatmapset),
    }


def _ai_beatmapset_payload(beatmapset: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": beatmapset.get("id"),
        "title": beatmapset.get("title"),
        "artist": beatmapset.get("artist"),
        "creator": beatmapset.get("creator"),
        "status": beatmapset.get("status"),
        "play_count": beatmapset.get("play_count"),
        "favourite_count": beatmapset.get("favourite_count"),
        "url": f"https://osu.ppy.sh/beatmapsets/{beatmapset.get('id')}" if beatmapset.get("id") else "",
    }


def _ai_ranking_payload(item: dict[str, Any]) -> dict[str, Any]:
    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    return {
        "rank": item.get("global_rank") or item.get("rank"),
        "pp": item.get("pp"),
        "hit_accuracy": item.get("hit_accuracy"),
        "play_count": item.get("play_count"),
        "ranked_score": item.get("ranked_score"),
        "user": {
            "id": user.get("id"),
            "username": user.get("username"),
            "country_code": user.get("country_code"),
            "profile_url": f"https://osu.ppy.sh/users/{user.get('id')}" if user.get("id") else "",
        },
    }


@register_ai_tool(
    "osu_user_lookup",
    plugin_name="osu_info",
    description="Look up a public osu! user profile. If target is omitted, the current QQ user's osu! binding is used when available.",
    parameters={
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "osu! username or user ID. Optional when the QQ user has a binding.",
            },
            "mode": {
                "type": "string",
                "description": "osu! ruleset.",
                "enum": ["osu", "taiko", "fruits", "mania"],
            },
        },
        "additionalProperties": False,
    },
)
async def ai_tool_osu_user_lookup(context: AIToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    if not _enabled():
        return {"error": "osu_info is disabled"}
    resolved = _ai_resolve_osu_target(context, arguments)
    if resolved is None:
        return {"error": "target is required when current QQ user has no osu! binding"}
    mode, target = resolved
    try:
        user = await _get_client().get_user(target, mode)
    except OsuNotFoundError:
        return {"mode": mode, "target": target, "not_found": True}
    except OsuApiError as e:
        logger.warning("[osu] AI Tool 用户查询失败 target=%r mode=%s: %s", target, mode, e)
        return {"mode": mode, "target": target, "error": str(e)}
    return {"mode": mode, "user": _ai_user_payload(user)}


@register_ai_tool(
    "osu_scores_lookup",
    plugin_name="osu_info",
    description="Look up a public osu! user's best, recent, or first-place scores.",
    parameters={
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "osu! username or user ID. Optional when the QQ user has a binding.",
            },
            "mode": {
                "type": "string",
                "description": "osu! ruleset.",
                "enum": ["osu", "taiko", "fruits", "mania"],
            },
            "score_type": {
                "type": "string",
                "description": "Score list type.",
                "enum": ["best", "recent", "firsts"],
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of scores to return.",
                "minimum": 1,
                "maximum": 10,
            },
        },
        "additionalProperties": False,
    },
)
async def ai_tool_osu_scores_lookup(context: AIToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    if not _enabled():
        return {"error": "osu_info is disabled"}
    resolved = _ai_resolve_osu_target(context, arguments)
    if resolved is None:
        return {"error": "target is required when current QQ user has no osu! binding"}
    mode, target = resolved
    score_type = str(arguments.get("score_type") or "best").strip().casefold()
    if score_type not in {"best", "recent", "firsts"}:
        score_type = "best"
    limit = _ai_int(arguments.get("limit"), default=int(get_config().get("score_limit") or 5), minimum=1, maximum=10)
    try:
        user = await _get_client().get_user(target, mode)
        scores = await _get_client().get_user_scores(int(user["id"]), mode, score_type, limit=limit)
    except OsuNotFoundError:
        return {"mode": mode, "target": target, "not_found": True}
    except OsuApiError as e:
        logger.warning("[osu] AI Tool 成绩查询失败 target=%r mode=%s type=%s: %s", target, mode, score_type, e)
        return {"mode": mode, "target": target, "score_type": score_type, "error": str(e)}
    return {
        "mode": mode,
        "score_type": score_type,
        "user": _ai_user_payload(user),
        "scores": [_ai_score_payload(score) for score in scores[:limit]],
    }


@register_ai_tool(
    "osu_beatmap_lookup",
    plugin_name="osu_info",
    description="Look up an osu! beatmap by beatmap ID/link or search beatmapsets by keyword.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Beatmap ID, beatmap URL, or search keyword.",
            },
            "mode": {
                "type": "string",
                "description": "osu! ruleset used for keyword search.",
                "enum": ["osu", "taiko", "fruits", "mania"],
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of keyword search results to return.",
                "minimum": 1,
                "maximum": 10,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)
async def ai_tool_osu_beatmap_lookup(context: AIToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    if not _enabled():
        return {"error": "osu_info is disabled"}
    query = str(arguments.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}
    mode = normalize_mode(str(arguments.get("mode") or ""), _default_mode())
    limit = _ai_int(arguments.get("limit"), default=int(get_config().get("beatmap_search_limit") or 5), minimum=1, maximum=10)
    try:
        beatmap_id = _extract_beatmap_id(query)
        if beatmap_id is not None:
            beatmap = await _get_client().get_beatmap(beatmap_id)
            return {"query": query, "mode": mode, "beatmap": _ai_beatmap_payload(beatmap)}
        result = await _get_client().search_beatmapsets(query, mode=mode)
    except OsuNotFoundError:
        return {"query": query, "mode": mode, "not_found": True}
    except OsuApiError as e:
        logger.warning("[osu] AI Tool 谱面查询失败 query=%r mode=%s: %s", query, mode, e)
        return {"query": query, "mode": mode, "error": str(e)}
    beatmapsets = result.get("beatmapsets") if isinstance(result, dict) else []
    if not isinstance(beatmapsets, list):
        beatmapsets = []
    return {
        "query": query,
        "mode": mode,
        "results": [_ai_beatmapset_payload(item) for item in beatmapsets[:limit] if isinstance(item, dict)],
    }


def _extract_beatmap_id(text: str) -> int | None:
    import re

    raw = text.strip()
    if raw.isdigit():
        return int(raw)
    patterns = [
        r"osu\.ppy\.sh/(?:beatmaps|b)/(\d+)",
        r"osu\.ppy\.sh/beatmapsets/\d+#\w+/(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw)
        if match:
            return int(match.group(1))
    return None


@register_ai_tool(
    "osu_ranking_lookup",
    plugin_name="osu_info",
    description="Look up public osu! global ranking entries by mode, optional country code, and optional mania variant.",
    parameters={
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "description": "osu! ruleset.",
                "enum": ["osu", "taiko", "fruits", "mania"],
            },
            "country": {
                "type": "string",
                "description": "Optional ISO country code such as JP or US.",
            },
            "variant": {
                "type": "string",
                "description": "Optional mania variant.",
                "enum": ["4k", "7k"],
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of ranking entries to return.",
                "minimum": 1,
                "maximum": 20,
            },
        },
        "additionalProperties": False,
    },
)
async def ai_tool_osu_ranking_lookup(context: AIToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    if not _enabled():
        return {"error": "osu_info is disabled"}
    mode = normalize_mode(str(arguments.get("mode") or ""), _default_mode())
    country = str(arguments.get("country") or "").strip().upper()[:2] or None
    variant = str(arguments.get("variant") or "").strip().casefold()
    if mode != "mania" or variant not in {"4k", "7k"}:
        variant = None
    limit = _ai_int(arguments.get("limit"), default=int(get_config().get("ranking_limit") or 10), minimum=1, maximum=20)
    try:
        data = await _get_client().get_ranking(mode, country=country, variant=variant)
    except OsuApiError as e:
        logger.warning("[osu] AI Tool 排名查询失败 mode=%s country=%s variant=%s: %s", mode, country, variant, e)
        return {"mode": mode, "country": country or "", "variant": variant or "", "error": str(e)}
    ranking = data.get("ranking") if isinstance(data, dict) else []
    if not isinstance(ranking, list):
        ranking = []
    return {
        "mode": mode,
        "country": country or "",
        "variant": variant or "",
        "ranking": [_ai_ranking_payload(item) for item in ranking[:limit] if isinstance(item, dict)],
    }
