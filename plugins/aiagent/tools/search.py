"""AI Agent 的网页搜索工具（`web_search`）。

三种模式，由 `tools.search.mode` 选择：

- `builtin`：DeepSeek 官方 Search API（``POST /v1/tools/search``），Responses 与
  Chat Completions 两种协议都能用。官方文档：
  https://api-docs.deepseek.com/zh-cn/guides/search
  非 ``api.deepseek.com`` 端点没有这个接口，会自动回退到 SearXNG。
- `searxng`：自建 SearXNG 实例。
- `hybrid`：并发 DeepSeek Search API 与自建 SearXNG，合并去重。

DeepSeek Search API 要点（按官方文档）：

- ``query`` 1~128 字符，支持 ``site:`` / ``-site:`` / ``-词`` / ``"短语"`` /
  ``lang:`` / ``after:`` / ``before:`` 等搜索语法；
- ``count`` 1~50（这里按上下文预算限制到 10）；
- ``ori_question`` 帮助理解搜索意图，``need_content`` 控制是否返回网页正文；
- 按成功请求计费（5 元 / 1000 次），``count`` 不影响单价；
- 408 / 429 / 5xx 可重试，其它 4xx 不要重试；账号级 QPS 为 5。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from ..utils import safe_bool, safe_int

logger = logging.getLogger("HikariBot.AIAgent.Tools.Search")

TOOL_NAME = "web_search"

# DeepSeek Search API 的硬性限制与上下文预算。
_MAX_QUERY_CHARS = 128
_MAX_ORI_QUESTION_CHARS = 128
_MAX_RESULTS = 10
_MAX_TOTAL_CHARS = 6000
_MAX_SUMMARY_CHARS = 600
_MAX_CONTENT_CHARS = 1500
_MAX_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 0.6
_RETRY_STATUSES = {408, 429, 500, 502, 503, 504}


def _tools_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    return cfg.get("tools") if isinstance(cfg.get("tools"), dict) else {}


def _model_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    return cfg.get("model") if isinstance(cfg.get("model"), dict) else {}


def config(cfg: dict[str, Any]) -> dict[str, Any]:
    tools_cfg = _tools_cfg(cfg)
    return tools_cfg.get("search") if isinstance(tools_cfg.get("search"), dict) else {}


def enabled(cfg: dict[str, Any]) -> bool:
    return safe_bool(config(cfg).get("enabled"), True)


def mode(cfg: dict[str, Any]) -> str:
    """搜索模式: `builtin`（DeepSeek 官方 Search API）、`searxng` 或 `hybrid`。"""
    value = str(config(cfg).get("mode") or "builtin").strip().lower()
    if value not in {"builtin", "searxng", "hybrid"}:
        return "builtin"
    return value


def _is_deepseek_endpoint(base_url: Any) -> bool:
    try:
        host = (urlparse(str(base_url or "")).hostname or "").strip().lower()
    except ValueError:
        return False
    return host == "api.deepseek.com" or host.endswith(".deepseek.com")


def backend(cfg: dict[str, Any]) -> str:
    """当前实际使用的后端：
    - `hybrid`：混合双引擎（DeepSeek + SearXNG 并发）
    - `searxng`：自建 SearXNG
    - `deepseek`：DeepSeek 官方搜索（非官方端点自动回退 searxng）
    """
    m = mode(cfg)
    if m == "hybrid":
        return "hybrid"
    if m == "searxng":
        return "searxng"
    return "deepseek" if _is_deepseek_endpoint(_model_cfg(cfg).get("base_url")) else "searxng"


def _deepseek_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": (
                "Search the web with DeepSeek's official search API. Use it for current events, "
                "prices, releases, or facts that may have changed. Prefer short keyword queries in "
                "the user's language; combine operators such as site:, lang:, after:, before:, "
                "quoted phrases and -exclusions when they improve precision. Results include a "
                "rerank score (>=1.5 is high quality) and authority (>=3 is highly authoritative); "
                "prefer those sources."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Search keywords (1-128 characters). Supports search operators: "
                            'site:example.com, -site:example.com, -word, -"exact phrase", '
                            '"exact phrase", lang:zh,en, after:2025-01-01, before:2026-06-01.'
                        ),
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Number of results to return (default from config, at most 10).",
                        "minimum": 1,
                        "maximum": _MAX_RESULTS,
                    },
                    "need_content": {
                        "type": "boolean",
                        "description": (
                            "Return the full page text for the results when summaries are not enough. "
                            "Larger and slower; defaults to false."
                        ),
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }


def _searxng_definition() -> dict[str, Any]:
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


def _hybrid_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": (
                "Search the web using a hybrid search engine combining DeepSeek official search "
                "and SearXNG. Use it for current events, facts, prices, or questions requiring "
                "external sources. Supports query keywords, operators (site:, etc.), and optional filters."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Search keywords (1-128 characters). Supports search operators: "
                            'site:example.com, -site:example.com, -word, -"exact phrase", '
                            '"exact phrase", lang:zh,en, after:2025-01-01, before:2026-06-01.'
                        ),
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return (default from config, at most 10).",
                        "minimum": 1,
                        "maximum": _MAX_RESULTS,
                    },
                    "need_content": {
                        "type": "boolean",
                        "description": (
                            "Return the full page text for the results when summaries are not enough. "
                            "Larger and slower; defaults to false."
                        ),
                    },
                    "categories": {
                        "type": "string",
                        "description": "Optional SearXNG categories, such as general, news, science.",
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


def definition(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """按当前后端返回工具定义（DeepSeek 官方搜索 / SearXNG / Hybrid 参数不同）。"""
    if cfg is not None:
        b = backend(cfg)
        if b == "hybrid":
            return _hybrid_definition()
        if b == "searxng":
            return _searxng_definition()
    return _deepseek_definition()


def deepseek_endpoint(base_url: Any) -> str:
    """把 base_url 归一成 ``/v1/tools/search``（兼容带 /v1 或完整路径的写法）。"""
    base = str(base_url or "https://api.deepseek.com").strip().rstrip("/")
    for suffix in ("/chat/completions", "/responses"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    if not base:
        base = "https://api.deepseek.com"
    if base.endswith("/tools/search"):
        return base
    if base.endswith("/v1"):
        return f"{base}/tools/search"
    return f"{base}/v1/tools/search"


def endpoint(base_url: Any) -> str:
    """SearXNG 搜索端点。"""
    base = str(base_url or "http://searxng-core:8080").strip().rstrip("/")
    if not base:
        base = "http://searxng-core:8080"
    if base.endswith("/search"):
        return base
    return f"{base}/search"


def normalize_url(raw_url: str) -> str:
    """规范化 URL 便于去重：统一 scheme/host（统一为 http、全小写、去 www.）、去 fragment 和末尾斜杠。"""
    try:
        parsed = urlparse(raw_url.strip())
        netloc = (parsed.netloc or "").lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        path = parsed.path.rstrip("/")
        query = parsed.query
        res = f"http://{netloc}{path}"
        if query:
            res = f"{res}?{query}"
        return res
    except Exception:
        return raw_url.strip()


async def execute(
    cfg: dict[str, Any], arguments: dict[str, Any], context: Any = None
) -> str:
    b = backend(cfg)
    if b == "hybrid":
        return await _execute_hybrid(cfg, arguments, context)
    if b == "searxng":
        return await _execute_searxng(cfg, arguments)
    return await _execute_deepseek(cfg, arguments, context)


# ── DeepSeek 官方 Search API ──────────────────────────────────────────────


def _original_question(context: Any) -> str:
    """当前用户原话，作为 `ori_question` 帮助搜索理解意图。"""
    event = getattr(context, "event", None)
    if event is None:
        return ""
    try:
        text = str(event.get_plaintext() or "")
    except Exception:
        return ""
    text = " ".join(text.split())
    return text[:_MAX_ORI_QUESTION_CHARS]


def _error(query: str, message: str, **extra: Any) -> str:
    payload: dict[str, Any] = {"query": query, "error": message}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def _error_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except Exception:
        return str(getattr(response, "text", "") or "")[:300]
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])[:300]
    return str(getattr(response, "text", "") or "")[:300]


async def _post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
) -> httpx.Response:
    """按官方文档重试 408 / 429 / 5xx，其它 4xx 直接返回。"""
    last_error: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            response = await client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as e:
            last_error = e
            if attempt == _MAX_ATTEMPTS - 1:
                raise
        else:
            if response.status_code not in _RETRY_STATUSES or attempt == _MAX_ATTEMPTS - 1:
                return response
        await asyncio.sleep(_RETRY_DELAY_SECONDS * (2**attempt))
    raise last_error or RuntimeError("DeepSeek search request failed")


def _shape_results(raw_results: Any, count: int, need_content: bool) -> list[dict[str, Any]]:
    """只保留模型真正需要、且总量可控的字段。"""
    results: list[dict[str, Any]] = []
    used_chars = 0
    for item in raw_results if isinstance(raw_results, list) else []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        summary = str(item.get("summary") or item.get("snippet") or "").strip()
        if not (title or url or summary):
            continue

        entry: dict[str, Any] = {
            "title": title[:200],
            "url": url[:500],
            "summary": summary[:_MAX_SUMMARY_CHARS],
        }
        site_name = str(item.get("site_name") or "").strip()
        if site_name:
            entry["site"] = site_name[:80]
        date = str(item.get("date") or "").strip()
        if date:
            entry["date"] = date[:40]
        score = item.get("rerank_score")
        if isinstance(score, (int, float)):
            entry["score"] = round(float(score), 2)
        authority = item.get("authority")
        if isinstance(authority, int):
            entry["authority"] = authority
        content = str(item.get("content") or "").strip()
        if need_content and content:
            entry["content"] = content[:_MAX_CONTENT_CHARS]

        size = len(json.dumps(entry, ensure_ascii=False))
        if results and used_chars + size > _MAX_TOTAL_CHARS:
            break
        results.append(entry)
        used_chars += size
        if len(results) >= count:
            break
    return results


async def _execute_deepseek(
    cfg: dict[str, Any], arguments: dict[str, Any], context: Any = None
) -> str:
    search_cfg = config(cfg)
    model_cfg = _model_cfg(cfg)
    query = str(arguments.get("query") or "").strip()
    if not query:
        return _error("", "query is required")
    query = query[:_MAX_QUERY_CHARS]

    api_key = str(model_cfg.get("api_key") or "").strip()
    if not api_key:
        return _error(
            query,
            "DeepSeek 搜索需要配置 API Key；也可以把 tools.search.mode 改为 searxng",
        )

    configured_max = safe_int(search_cfg.get("max_results"), 5, minimum=1, maximum=_MAX_RESULTS)
    count = safe_int(arguments.get("max_results"), configured_max, minimum=1, maximum=_MAX_RESULTS)
    need_content = safe_bool(
        arguments.get("need_content"), safe_bool(search_cfg.get("need_content"), False)
    )

    payload: dict[str, Any] = {
        "query": query,
        "count": count,
        "need_content": need_content,
    }
    ori_question = _original_question(context)
    if ori_question:
        payload["ori_question"] = ori_question

    timeout = httpx.Timeout(
        safe_int(search_cfg.get("timeout_seconds"), 30, minimum=1, maximum=120)
    )
    proxy = str(model_cfg.get("proxy") or search_cfg.get("proxy") or "").strip() or None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    url = deepseek_endpoint(model_cfg.get("base_url"))

    try:
        async with httpx.AsyncClient(timeout=timeout, proxy=proxy) as client:
            response = await _post_with_retry(client, url, headers, payload)
    except httpx.HTTPError as e:
        logger.warning("[AIAgent] DeepSeek 搜索请求失败: %s", e)
        return _error(query, f"search request failed: {e}")

    if response.status_code >= 400:
        logger.warning("[AIAgent] DeepSeek 搜索返回 HTTP %s", response.status_code)
        return _error(
            query,
            f"DeepSeek search HTTP {response.status_code}",
            detail=_error_detail(response),
        )

    try:
        data = response.json()
    except Exception:
        return _error(query, "DeepSeek search returned invalid JSON")

    raw_results = data.get("results") if isinstance(data, dict) else []
    results = _shape_results(raw_results, count, need_content)
    return json.dumps(
        {"query": query, "results": results},
        ensure_ascii=False,
    )


# ── 自建 SearXNG ──────────────────────────────────────────────────────────


async def _execute_searxng(cfg: dict[str, Any], arguments: dict[str, Any]) -> str:
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
    try:
        async with httpx.AsyncClient(timeout=timeout, proxy=proxy) as client:
            response = await client.get(endpoint(search_cfg.get("base_url")), params=params)
    except Exception as e:
        logger.warning("[AIAgent] SearXNG 搜索请求失败: %s", e)
        return json.dumps(
            {"query": query, "error": f"SearXNG search failed: {e}"},
            ensure_ascii=False,
        )

    if response.status_code >= 400:
        return json.dumps(
            {"query": query, "error": f"SearXNG HTTP {response.status_code}", "detail": response.text[:300]},
            ensure_ascii=False,
        )

    try:
        data = response.json()
    except Exception:
        return json.dumps({"query": query, "error": "SearXNG returned invalid JSON"}, ensure_ascii=False)

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


# ── Hybrid 混合搜索 ───────────────────────────────────────────────────────


async def _fetch_deepseek_items(
    cfg: dict[str, Any], arguments: dict[str, Any], context: Any = None
) -> tuple[list[dict[str, Any]], str | None]:
    search_cfg = config(cfg)
    model_cfg = _model_cfg(cfg)
    query = str(arguments.get("query") or "").strip()[:_MAX_QUERY_CHARS]
    api_key = str(model_cfg.get("api_key") or "").strip()
    if not api_key:
        return [], "no_api_key"

    configured_max = safe_int(search_cfg.get("max_results"), 5, minimum=1, maximum=_MAX_RESULTS)
    count = safe_int(arguments.get("max_results"), configured_max, minimum=1, maximum=_MAX_RESULTS)
    need_content = safe_bool(
        arguments.get("need_content"), safe_bool(search_cfg.get("need_content"), False)
    )

    payload: dict[str, Any] = {
        "query": query,
        "count": count,
        "need_content": need_content,
    }
    ori_question = _original_question(context)
    if ori_question:
        payload["ori_question"] = ori_question

    timeout = httpx.Timeout(
        safe_int(search_cfg.get("timeout_seconds"), 30, minimum=1, maximum=120)
    )
    proxy = str(model_cfg.get("proxy") or search_cfg.get("proxy") or "").strip() or None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    url = deepseek_endpoint(model_cfg.get("base_url"))

    try:
        async with httpx.AsyncClient(timeout=timeout, proxy=proxy) as client:
            response = await _post_with_retry(client, url, headers, payload)
    except Exception as e:
        logger.warning("[AIAgent] Hybrid DeepSeek 搜索请求失败: %s", e)
        return [], str(e)

    if response.status_code >= 400:
        logger.warning("[AIAgent] Hybrid DeepSeek 搜索返回 HTTP %s", response.status_code)
        return [], f"HTTP {response.status_code}"

    try:
        data = response.json()
    except Exception:
        return [], "invalid JSON"

    raw_results = data.get("results") if isinstance(data, dict) else []
    return _shape_results(raw_results, count, need_content), None


async def _fetch_searxng_items(
    cfg: dict[str, Any], arguments: dict[str, Any]
) -> tuple[list[dict[str, Any]], str | None]:
    search_cfg = config(cfg)
    query = str(arguments.get("query") or "").strip()
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
    try:
        async with httpx.AsyncClient(timeout=timeout, proxy=proxy) as client:
            response = await client.get(endpoint(search_cfg.get("base_url")), params=params)
    except Exception as e:
        logger.warning("[AIAgent] Hybrid SearXNG 搜索请求失败: %s", e)
        return [], str(e)

    if response.status_code >= 400:
        logger.warning("[AIAgent] Hybrid SearXNG 搜索 HTTP %d", response.status_code)
        return [], f"HTTP {response.status_code}"

    try:
        data = response.json()
    except Exception:
        return [], "invalid JSON"

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
    return results, None


async def _execute_hybrid(
    cfg: dict[str, Any], arguments: dict[str, Any], context: Any = None
) -> str:
    query = str(arguments.get("query") or "").strip()
    if not query:
        return _error("", "query is required")

    search_cfg = config(cfg)
    model_cfg = _model_cfg(cfg)
    api_key = str(model_cfg.get("api_key") or "").strip()
    base_url = model_cfg.get("base_url")

    # 失败降级：DeepSeek 没 Key / 非 api.deepseek.com 端点：hybrid 自动只用 SearXNG，不报错。
    has_deepseek = bool(api_key and _is_deepseek_endpoint(base_url))

    configured_max = safe_int(search_cfg.get("max_results"), 5, minimum=1, maximum=10)
    max_results = safe_int(arguments.get("max_results"), configured_max, minimum=1, maximum=10)

    errors: dict[str, str] = {}
    ds_items: list[dict[str, Any]] = []
    sx_items: list[dict[str, Any]] = []

    if has_deepseek:
        res1, res2 = await asyncio.gather(
            _fetch_deepseek_items(cfg, arguments, context),
            _fetch_searxng_items(cfg, arguments),
            return_exceptions=True,
        )
        if isinstance(res1, Exception):
            errors["deepseek"] = str(res1)
        else:
            ds_items, ds_err = res1
            if ds_err:
                errors["deepseek"] = ds_err

        if isinstance(res2, Exception):
            errors["searxng"] = str(res2)
        else:
            sx_items, sx_err = res2
            if sx_err:
                errors["searxng"] = sx_err
    else:
        res2 = await _fetch_searxng_items(cfg, arguments)
        sx_items, sx_err = res2
        if sx_err:
            errors["searxng"] = sx_err

    # 两边都失败才返回错误
    if has_deepseek and errors.get("deepseek") and errors.get("searxng"):
        return _error(query, "both search engines failed", errors=errors)
    if not has_deepseek and errors.get("searxng"):
        return _error(query, f"SearXNG search failed: {errors['searxng']}", errors=errors)

    # 标记来源
    for item in ds_items:
        item["source"] = "deepseek"
    for item in sx_items:
        item["source"] = "searxng"

    # URL 去重：
    # 统一 scheme/host、去掉 www.、去掉 fragment 和末尾 /
    # 同一 URL 保留 DeepSeek 的 summary / score / authority，SearXNG 的 content 作为补充
    ds_by_url: dict[str, dict[str, Any]] = {}
    for item in ds_items:
        norm = normalize_url(item.get("url", ""))
        if norm:
            ds_by_url[norm] = item

    sx_deduped: list[dict[str, Any]] = []
    for item in sx_items:
        norm = normalize_url(item.get("url", ""))
        if norm and norm in ds_by_url:
            ds_match = ds_by_url[norm]
            if not ds_match.get("content") and item.get("content"):
                ds_match["content"] = item["content"]
        else:
            sx_deduped.append(item)

    # DeepSeek 结果按 rerank_score 降序，SearXNG 结果穿插补充
    combined: list[dict[str, Any]] = []
    ds_i = 0
    sx_i = 0
    while (ds_i < len(ds_items) or sx_i < len(sx_deduped)) and len(combined) < max_results:
        if ds_i < len(ds_items):
            combined.append(ds_items[ds_i])
            ds_i += 1
        if len(combined) >= max_results:
            break
        if sx_i < len(sx_deduped):
            combined.append(sx_deduped[sx_i])
            sx_i += 1

    ds_count = sum(1 for x in combined if x.get("source") == "deepseek")
    sx_count = sum(1 for x in combined if x.get("source") == "searxng")
    sources = {"deepseek": ds_count, "searxng": sx_count}

    payload: dict[str, Any] = {
        "query": query,
        "results": combined,
        "sources": sources,
    }
    if errors:
        payload["errors"] = errors

    return json.dumps(payload, ensure_ascii=False)
