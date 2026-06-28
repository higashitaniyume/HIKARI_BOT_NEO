from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from ..utils import safe_bool, safe_int

logger = logging.getLogger("HikariBot.AIAgent.Tools.Search")

TOOL_NAME = "web_search"


def _tools_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    return cfg.get("tools") if isinstance(cfg.get("tools"), dict) else {}


def config(cfg: dict[str, Any]) -> dict[str, Any]:
    tools_cfg = _tools_cfg(cfg)
    return tools_cfg.get("search") if isinstance(tools_cfg.get("search"), dict) else {}


def enabled(cfg: dict[str, Any]) -> bool:
    return safe_bool(config(cfg).get("enabled"), True)


def definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
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


def endpoint(base_url: Any) -> str:
    base = str(base_url or "http://searxng-core:8080").strip().rstrip("/")
    if not base:
        base = "http://searxng-core:8080"
    if base.endswith("/search"):
        return base
    return f"{base}/search"


async def execute(cfg: dict[str, Any], arguments: dict[str, Any]) -> str:
    search_cfg = config(cfg)
    query = str(arguments.get("query") or "").strip()
    if not query:
        return json.dumps({"error": "query is required"}, ensure_ascii=False)

    configured_max = safe_int(search_cfg.get("max_results"), 5, minimum=1, maximum=10)
    max_results = safe_int(arguments.get("max_results"), configured_max, minimum=1, maximum=10)
    categories = str(arguments.get("categories") or search_cfg.get("categories") or "general").strip()
    language = str(arguments.get("language") or search_cfg.get("language") or "auto").strip()
    time_range = str(arguments.get("time_range") or "").strip()
    safesearch = safe_int(search_cfg.get("safesearch"), 1, minimum=0, maximum=2)

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

    timeout = httpx.Timeout(safe_int(search_cfg.get("timeout_seconds"), 15, minimum=1, maximum=120))
    proxy = str(search_cfg.get("proxy") or "").strip() or None
    async with httpx.AsyncClient(timeout=timeout, proxy=proxy) as client:
        response = await client.get(endpoint(search_cfg.get("base_url")), params=params)
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
