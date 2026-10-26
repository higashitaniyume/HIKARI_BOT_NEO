"""AI 搜图的 Pixiv 优先数据源。

设计：

- 复用 `pixiv_parser` 的 Cookie / 代理配置，只读不改；没有 Cookie 就交给搜索引擎。
- 搜索走 Pixiv Web Ajax：`GET /ajax/search/artworks/{keyword}`。当前线上响应把作品放在
  `body.illustManga.data`（旧文档写的是 `body.illust.data`），这里两种都兼容。
- 搜索结果项只给 250px 缩略图（`url`），所以每个候选作品再取一次
  `GET /ajax/illust/{id}/pages`，用第一页的 `regular` 大图作为 `image_url`。
- `i.pximg.net` 防盗链只认 Referer，下载时必须带 `Referer: https://www.pixiv.net/`，
  否则固定 403；`download_options()` 负责提供这套请求头（含 Cookie，可选）。
- 任何异常都只记日志并返回空结果，让调用方无缝回退到 SearXNG。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from core.config_loader import load_plugin_config
from core.defaults import DEFAULT_PIXIV_CONFIG

from ..utils import safe_bool, safe_int

logger = logging.getLogger("HikariBot.AIAgent.Tools.PixivImages")

PIXIV_WEB_BASE = "https://www.pixiv.net"
# 与 pixiv_parser 保持一致的浏览器 UA；Referer 缺失时 i.pximg.net 会直接 403。
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.7103.48 Safari/537.36"
)
PIXIV_ACCEPT_LANGUAGE = (
    "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7,ja-JP;q=0.6,ja;q=0.5"
)

DEFAULT_PIXIV_RESULTS = 3
MAX_PIXIV_RESULTS = 10
DEFAULT_TIMEOUT_SECONDS = 15
# 一个作品可能没有可发的大图；多取一些候选，够 limit 就停。
_CANDIDATE_MULTIPLIER = 2
_CANDIDATE_EXTRA = 2
_MAX_CANDIDATES = 20

_IMAGE_HOST_SUFFIX = "pximg.net"


# ── 配置 ──────────────────────────────────────────────────────────────────


def config(cfg: dict[str, Any]) -> dict[str, Any]:
    tools = cfg.get("tools") if isinstance(cfg, dict) else None
    images = tools.get("images") if isinstance(tools, dict) else None
    section = images.get("pixiv") if isinstance(images, dict) else None
    return section if isinstance(section, dict) else {}


def enabled(cfg: dict[str, Any]) -> bool:
    return safe_bool(config(cfg).get("enabled"), True)


def max_results(cfg: dict[str, Any]) -> int:
    return safe_int(
        config(cfg).get("max_results"),
        DEFAULT_PIXIV_RESULTS,
        minimum=1,
        maximum=MAX_PIXIV_RESULTS,
    )


def fallback_web(cfg: dict[str, Any]) -> bool:
    return safe_bool(config(cfg).get("fallback_web"), True)


def order(cfg: dict[str, Any]) -> str:
    value = str(config(cfg).get("order") or "").strip().lower()
    return value if value in {"date_d", "date"} else "date_d"


def timeout_seconds(cfg: dict[str, Any]) -> int:
    return safe_int(
        config(cfg).get("timeout_seconds"),
        DEFAULT_TIMEOUT_SECONDS,
        minimum=3,
        maximum=60,
    )


def _pixiv_plugin_config() -> dict[str, Any]:
    try:
        return load_plugin_config("pixiv_parser", DEFAULT_PIXIV_CONFIG)
    except Exception as e:
        logger.warning("[AIAgent] 读取 Pixiv 插件配置失败: %s", e)
        return {}


def is_pixiv_image_url(url: str) -> bool:
    try:
        host = (urlparse(str(url or "")).hostname or "").strip().lower().rstrip(".")
    except Exception:
        return False
    return bool(host) and (host == _IMAGE_HOST_SUFFIX or host.endswith(f".{_IMAGE_HOST_SUFFIX}"))


def download_options() -> tuple[dict[str, str], str | None]:
    """Pixiv 图片下载所需的请求头与代理（i.pximg.net 必须带 Referer）。"""
    plugin_cfg = _pixiv_plugin_config()
    headers = {
        "accept-language": PIXIV_ACCEPT_LANGUAGE,
        "user-agent": USER_AGENT,
        "referer": f"{PIXIV_WEB_BASE}/",
    }
    cookie = str(plugin_cfg.get("cookie") or "").strip()
    if cookie:
        headers["cookie"] = cookie
    proxy = str(plugin_cfg.get("proxy") or "").strip() or None
    return headers, proxy


# ── 搜索 ──────────────────────────────────────────────────────────────────


async def search(
    cfg: dict[str, Any],
    query: str,
    limit: int,
    safesearch: int = 1,
) -> dict[str, Any]:
    """搜 Pixiv 插画；返回 `{"results": [...], "error"/"note": ...}`，不抛异常。"""
    word = " ".join(str(query or "").split()).strip()
    if not word:
        return {"results": [], "error": "query is required"}
    limit = max(1, min(int(limit or DEFAULT_PIXIV_RESULTS), MAX_PIXIV_RESULTS))

    plugin_cfg = _pixiv_plugin_config()
    cookie = str(plugin_cfg.get("cookie") or "").strip()
    if not cookie:
        return {"results": [], "note": "Pixiv Cookie 未配置，已跳过 Pixiv"}

    proxy = str(plugin_cfg.get("proxy") or "").strip() or None
    # R18 只在「pixiv 插件允许 R18」且本次搜图 safesearch=0 时放开
    allow_r18 = safe_bool(plugin_cfg.get("allow_r18"), False) and int(safesearch or 0) == 0
    mode = "all" if allow_r18 else "safe"
    headers = {
        "accept-language": PIXIV_ACCEPT_LANGUAGE,
        "user-agent": USER_AGENT,
        "referer": f"{PIXIV_WEB_BASE}/",
        "cookie": cookie,
    }
    timeout = timeout_seconds(cfg)

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers=headers,
            proxy=proxy,
            follow_redirects=True,
        ) as client:
            items: list[dict[str, Any]] = []
            # 先按完整标签精确搜；没有可用作品时退一步做标签部分匹配
            for s_mode in ("s_tag_full", "s_tag"):
                items = await _search_items(client, word, mode, order(cfg), s_mode)
                if any(_item_allowed(item, allow_r18) for item in items):
                    break
            if not items:
                return {"results": [], "note": "Pixiv 没有找到相关作品"}

            candidates = [item for item in items if _item_allowed(item, allow_r18)]
            candidates = candidates[: max(limit * _CANDIDATE_MULTIPLIER, limit + _CANDIDATE_EXTRA)]
            candidates = candidates[:_MAX_CANDIDATES]
            results = await _resolve_pages(client, candidates, limit)
    except Exception as e:
        logger.warning("[AIAgent] Pixiv 搜图失败 query=%.60s: %s", word, e)
        return {"results": [], "error": f"Pixiv 搜图失败: {type(e).__name__}"}

    if not results:
        return {"results": [], "note": "Pixiv 有结果，但没有取到可用的大图"}
    logger.info("[AIAgent] Pixiv 搜图命中 %d 张 -> %.60s", len(results), word)
    return {"results": results}


async def _search_items(
    client: httpx.AsyncClient,
    word: str,
    mode: str,
    order_value: str,
    s_mode: str,
) -> list[dict[str, Any]]:
    url = f"{PIXIV_WEB_BASE}/ajax/search/artworks/{quote(word, safe='')}"
    params = {
        "word": word,
        "type": "all",
        "order": order_value,
        "mode": mode,
        "s_mode": s_mode,
        "p": 1,
        "lang": "zh",
    }
    try:
        response = await client.get(url, params=params)
    except Exception as e:
        logger.warning("[AIAgent] Pixiv 搜索请求失败 s_mode=%s: %s", s_mode, e)
        return []
    if response.status_code != 200:
        logger.warning(
            "[AIAgent] Pixiv 搜索 HTTP %d s_mode=%s: %.120s",
            response.status_code,
            s_mode,
            getattr(response, "text", ""),
        )
        return []
    try:
        data = response.json()
    except Exception:
        logger.warning("[AIAgent] Pixiv 搜索返回非 JSON（可能被 Cloudflare 拦截）")
        return []
    if not isinstance(data, dict) or data.get("error"):
        return []
    body = data.get("body") if isinstance(data.get("body"), dict) else {}
    # 线上字段是 illustManga；旧文档/旧接口是 illust，两种都认
    for key in ("illustManga", "illust"):
        section = body.get(key)
        if not isinstance(section, dict):
            continue
        items = section.get("data")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def _item_allowed(item: dict[str, Any], allow_r18: bool) -> bool:
    try:
        x_restrict = int(item.get("xRestrict") or 0)
        illust_type = int(item.get("illustType") or 0)
        page_count = int(item.get("pageCount") or 0)
    except (TypeError, ValueError):
        return False
    if not str(item.get("id") or "").isdigit():
        return False
    if x_restrict and not allow_r18:
        return False
    if bool(item.get("isMasked")) and not allow_r18:
        return False
    # 0=插画 1=漫画 2=动图（动图没有可直接发送的首帧大图，跳过）
    if illust_type not in (0, 1):
        return False
    return page_count >= 1


async def _resolve_pages(
    client: httpx.AsyncClient,
    candidates: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    responses = await asyncio.gather(
        *(_fetch_first_page(client, item) for item in candidates),
        return_exceptions=True,
    )
    results: list[dict[str, Any]] = []
    for item, page in zip(candidates, responses):
        if isinstance(page, BaseException) or not isinstance(page, dict):
            continue
        result = _build_result(item, page)
        if result is not None:
            results.append(result)
        if len(results) >= limit:
            break
    return results


async def _fetch_first_page(
    client: httpx.AsyncClient, item: dict[str, Any]
) -> dict[str, Any] | None:
    illust_id = str(item.get("id") or "")
    if not illust_id.isdigit():
        return None
    try:
        response = await client.get(
            f"{PIXIV_WEB_BASE}/ajax/illust/{illust_id}/pages", params={"lang": "zh"}
        )
    except Exception as e:
        logger.debug("[AIAgent] 获取 Pixiv 作品页失败 id=%s: %s", illust_id, e)
        return None
    if response.status_code != 200:
        return None
    try:
        data = response.json()
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("error"):
        return None
    pages = data.get("body")
    if not isinstance(pages, list) or not pages:
        return None
    first = pages[0] if isinstance(pages[0], dict) else {}
    urls = first.get("urls")
    return urls if isinstance(urls, dict) else None


def _build_result(
    item: dict[str, Any], urls: dict[str, Any]
) -> dict[str, Any] | None:
    image_url = str(
        urls.get("regular") or urls.get("small") or urls.get("original") or ""
    ).strip()
    if not image_url:
        return None
    illust_id = str(item.get("id") or "")
    suffix = Path(urlparse(image_url).path).suffix.lower().lstrip(".") or "jpg"
    try:
        width = int(item.get("width") or 0)
        height = int(item.get("height") or 0)
    except (TypeError, ValueError):
        width = height = 0
    tags = [
        str(tag).strip()
        for tag in (item.get("tags") if isinstance(item.get("tags"), list) else [])
        if str(tag).strip()
    ][:8]
    return {
        "title": str(item.get("title") or "").strip()[:200],
        "page_url": f"{PIXIV_WEB_BASE}/artworks/{illust_id}",
        "image_url": image_url,
        "thumbnail_url": str(item.get("url") or "").strip(),
        "resolution": f"{width}x{height}" if width or height else "",
        "format": suffix,
        "engine": "pixiv",
        "illust_id": illust_id,
        "artist": str(item.get("userName") or "").strip()[:100],
        "tags": tags,
    }
