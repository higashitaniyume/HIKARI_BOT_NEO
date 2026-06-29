from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date
from html import unescape
from html.parser import HTMLParser
from typing import Any, Literal

import httpx

from .storage import annotate_price_changes


class SteamDealsError(RuntimeError):
    pass


DealMode = Literal["all", "free", "low"]


@dataclass(slots=True)
class SteamDeal:
    appid: int
    name: str
    url: str
    image_url: str
    discount_percent: int
    original_price_cents: int
    final_price_cents: int
    currency: str
    source: str = ""
    released: str = ""
    review_summary: str = ""
    review_percent: int = 0
    review_count: int = 0
    promotion_kind: str = ""
    promotion_start: str = ""
    promotion_end: str = ""
    market_rank: int = 0
    categories: set[str] = field(default_factory=set)

    @property
    def is_free(self) -> bool:
        return self.final_price_cents <= 0


_CACHE: dict[str, tuple[float, list[SteamDeal]]] = {}


class SteamDealsClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.api_url = str(config.get("api_url") or "").strip()
        self.search_url = str(config.get("search_url") or "https://store.steampowered.com/search/results/").strip()
        self.steamdb_free_url = str(config.get("steamdb_free_url") or "https://steamdb.info/upcoming/free/").strip()
        self.country = str(config.get("country") or "cn").strip().lower()
        self.language = str(config.get("language") or "schinese").strip()
        self.timeout = float(config.get("timeout") or 20)
        self.proxy = str(config.get("proxy") or "").strip() or None
        self.cache_ttl = max(0, int(config.get("cache_ttl_minutes") or 30)) * 60
        if not self.api_url:
            raise SteamDealsError("Steam 特惠 API 地址未配置")

    def _client_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(self.timeout, connect=min(self.timeout, 10.0)),
            "follow_redirects": True,
            "headers": {
                "Accept": "application/json",
                "User-Agent": "HIKARI_BOT_NEO steam_deals",
            },
        }
        if self.proxy:
            kwargs["proxy"] = self.proxy
        return kwargs

    async def fetch_deals(self, *, force_refresh: bool = False) -> list[SteamDeal]:
        cache_key = (
            f"{self.api_url}|{self.search_url}|{self.country}|{self.language}|"
            f"{self.config.get('include_search_results')}|{self.config.get('search_pages')}|"
            f"{self.config.get('search_count_per_page')}|{self.config.get('search_sort_by')}|"
            f"{self.config.get('search_category1')}|"
            f"{self.config.get('include_market_results')}|{self.config.get('market_filters')}|"
            f"{self.config.get('market_pages')}|{self.config.get('market_count_per_page')}|"
            f"{self.config.get('daily_filter')}|"
            f"{self.config.get('price_watch')}|"
            f"{self.config.get('include_steamdb_free_promotions')}|{self.steamdb_free_url}"
        )
        cached = _CACHE.get(cache_key)
        now = time.monotonic()
        if not force_refresh and cached and now - cached[0] < self.cache_ttl:
            return list(cached[1])

        async with httpx.AsyncClient(**self._client_kwargs()) as client:
            deals = await self._fetch_featured_deals(client)
            if self.config.get("include_market_results", True):
                deals = self._merge_deals(deals, await self._fetch_market_deals(client))
            if self.config.get("include_search_results", True):
                deals = self._merge_deals(deals, await self._fetch_search_deals(client))
            if self.config.get("include_steamdb_free_promotions", True):
                deals = self._merge_deals(deals, await self._fetch_steamdb_promotions(client))
        price_watch = self.config.get("price_watch") or {}
        annotate_price_changes(
            deals,
            enabled=bool(price_watch.get("enabled", True)),
            mark_first_seen_as_new=bool(price_watch.get("mark_first_seen_as_new", True)),
            max_entries=max(100, int(price_watch.get("max_entries") or 5000)),
        )
        _CACHE[cache_key] = (now, deals)
        return list(deals)

    async def _fetch_featured_deals(self, client: httpx.AsyncClient) -> list[SteamDeal]:
        try:
            response = await client.get(
                self.api_url,
                params={"cc": self.country, "l": self.language},
            )
            response.raise_for_status()
            data = response.json()
        except httpx.RequestError as e:
            raise SteamDealsError(f"Steam 商店连接失败: {type(e).__name__}") from e
        except httpx.HTTPStatusError as e:
            raise SteamDealsError(f"Steam 商店请求失败: HTTP {e.response.status_code}") from e
        except ValueError as e:
            raise SteamDealsError("Steam 商店返回内容不是有效 JSON") from e
        return self._parse_featured_categories(data)

    async def _fetch_search_deals(self, client: httpx.AsyncClient) -> list[SteamDeal]:
        if not self.search_url:
            return []
        pages = max(1, min(int(self.config.get("search_pages") or 2), 10))
        count = max(10, min(int(self.config.get("search_count_per_page") or 50), 100))
        sort_values = self.config.get("search_sort_by") or ["Released_DESC"]
        sort_by_values = [str(sort_values)] if isinstance(sort_values, str) else [str(item) for item in sort_values]
        search_category = _safe_int(self.config.get("search_category1"))

        deals: list[SteamDeal] = []
        for sort_by in sort_by_values:
            for page in range(pages):
                params: dict[str, Any] = {
                    "query": "",
                    "start": page * count,
                    "count": count,
                    "dynamic_data": "",
                    "sort_by": sort_by,
                    "specials": 1,
                    "cc": self.country,
                    "l": self.language,
                    "infinite": 1,
                }
                if search_category > 0:
                    params["category1"] = search_category
                try:
                    response = await client.get(
                        self.search_url,
                        params=params,
                    )
                    response.raise_for_status()
                    data = response.json()
                except (httpx.RequestError, httpx.HTTPStatusError, ValueError):
                    continue
                html = data.get("results_html") if isinstance(data, dict) else None
                if not isinstance(html, str) or not html.strip():
                    continue
                deals.extend(self._fetch_search_deals_from_html(html))
        return deals

    async def _fetch_market_deals(self, client: httpx.AsyncClient) -> list[SteamDeal]:
        if not self.search_url:
            return []
        filters = self.config.get("market_filters") or ["topsellers"]
        filter_values = [str(filters)] if isinstance(filters, str) else [str(item) for item in filters]
        pages = max(1, min(int(self.config.get("market_pages") or 1), 5))
        count = max(10, min(int(self.config.get("market_count_per_page") or 50), 100))
        search_category = _safe_int(self.config.get("search_category1"))

        deals: list[SteamDeal] = []
        seen: set[int] = set()
        for filter_name in filter_values:
            source = _market_source(filter_name)
            for page in range(pages):
                params: dict[str, Any] = {
                    "query": "",
                    "start": page * count,
                    "count": count,
                    "dynamic_data": "",
                    "filter": filter_name,
                    "cc": self.country,
                    "l": self.language,
                    "infinite": 1,
                }
                if search_category > 0:
                    params["category1"] = search_category
                try:
                    response = await client.get(self.search_url, params=params)
                    response.raise_for_status()
                    data = response.json()
                except (httpx.RequestError, httpx.HTTPStatusError, ValueError):
                    continue
                html = data.get("results_html") if isinstance(data, dict) else None
                if not isinstance(html, str) or not html.strip():
                    continue
                for deal in self._fetch_search_deals_from_html(html):
                    if deal.appid in seen:
                        continue
                    seen.add(deal.appid)
                    deal.source = source
                    deal.market_rank = len(deals) + 1
                    deal.categories.add(source)
                    deals.append(deal)
        return deals

    def _fetch_search_deals_from_html(self, html: str) -> list[SteamDeal]:
        return _SearchResultParser().parse(html)

    async def _fetch_steamdb_promotions(self, client: httpx.AsyncClient) -> list[SteamDeal]:
        if not self.steamdb_free_url:
            return []
        try:
            response = await client.get(
                self.steamdb_free_url,
                headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "User-Agent": "Mozilla/5.0 HIKARI_BOT_NEO steam_deals",
                },
            )
            response.raise_for_status()
        except (httpx.RequestError, httpx.HTTPStatusError):
            return []
        return self._fetch_steamdb_promotions_from_html(response.text)

    def _fetch_steamdb_promotions_from_html(self, html: str) -> list[SteamDeal]:
        return _SteamDbPromotionParser().parse(html)

    def filter_deals(self, deals: list[SteamDeal], mode: DealMode = "all") -> list[SteamDeal]:
        max_low_price = max(0, int(self.config.get("max_low_price_cents") or 1000))
        min_discount = max(0, min(int(self.config.get("min_discount_percent") or 90), 100))
        limit = max(1, int(self.config.get("max_items") or 18))
        daily_cfg = self.config.get("daily_filter") or {}
        max_search_age_days = max(0, int(daily_cfg.get("max_search_release_age_days") or 730))
        min_recent_discount = max(0, min(int(daily_cfg.get("min_discount_for_recent_deal") or 20), 100))

        filtered: list[SteamDeal] = []
        for deal in deals:
            free = deal.is_free
            low = 0 < deal.final_price_cents <= max_low_price
            big_discount = deal.discount_percent >= min_discount
            changed_discount = _is_changed_discount(deal)
            market_item = _is_market_item(deal)
            recent_discount = (
                deal.discount_percent >= min_recent_discount
                and _is_recent_release(deal, max_search_age_days)
            )
            if free:
                deal.categories.add("免费")
            if low:
                deal.categories.add("低价")
            if big_discount:
                deal.categories.add("大折扣")
            if deal.promotion_kind == "free_to_keep":
                deal.categories.add("限免领取")
            elif deal.promotion_kind == "play_for_free":
                deal.categories.add("免费试玩")
            if _is_recent_release(deal, int((self.config.get("daily_filter") or {}).get("max_search_release_age_days") or 730)):
                deal.categories.add("近期")
            if deal.source:
                deal.categories.add(deal.source)

            promoted_free = deal.promotion_kind in {"free_to_keep", "play_for_free"}
            if mode == "free" and (free or promoted_free):
                filtered.append(deal)
            elif mode == "low" and not free and (low or big_discount):
                filtered.append(deal)
            elif mode == "all" and (market_item or free or low or big_discount or promoted_free or changed_discount or recent_discount):
                filtered.append(deal)

        if mode == "all" and (self.config.get("daily_filter") or {}).get("enabled", True):
            return self._apply_daily_filter(filtered, limit, max_low_price, min_discount)

        filtered.sort(
            key=lambda item: (
                _promotion_sort(item),
                item.final_price_cents,
                -item.discount_percent,
                item.name.casefold(),
            )
        )
        return filtered[:limit]

    def _apply_daily_filter(
        self,
        deals: list[SteamDeal],
        limit: int,
        max_low_price: int,
        min_discount: int,
    ) -> list[SteamDeal]:
        cfg = self.config.get("daily_filter") or {}
        max_per_family = max(1, int(cfg.get("max_per_title_family") or 2))
        min_reviews = max(0, int(cfg.get("min_review_count_for_plain_low_price") or 20))
        min_low_discount = max(0, min(int(cfg.get("min_discount_for_plain_low_price") or 80), 100))
        min_recent_discount = max(0, min(int(cfg.get("min_discount_for_recent_deal") or 20), 100))
        max_plain_low = max(0, int(cfg.get("max_plain_low_price_items") or 4))
        max_search_age_days = max(0, int(cfg.get("max_search_release_age_days") or 730))
        require_recent_search = bool(cfg.get("require_recent_search_results", True))

        qualified: list[SteamDeal] = []
        for deal in deals:
            if _daily_rank(
                deal,
                max_low_price,
                min_discount,
                min_reviews,
                min_low_discount,
                min_recent_discount,
                max_search_age_days,
                require_recent_search,
            ) >= 90:
                continue
            qualified.append(deal)

        result: list[SteamDeal] = []
        family_counts: dict[str, int] = {}
        plain_low_count = 0
        for deal in sorted(
            qualified,
            key=lambda item: _daily_sort_key(
                item,
                max_low_price,
                min_discount,
                min_reviews,
                min_low_discount,
                min_recent_discount,
                max_search_age_days,
                require_recent_search,
            ),
        ):
            family = _title_family(deal.name)
            if family_counts.get(family, 0) >= max_per_family:
                continue
            if _is_plain_low_price(deal, max_low_price, min_discount, min_reviews, min_low_discount):
                if plain_low_count >= max_plain_low:
                    continue
                plain_low_count += 1
            result.append(deal)
            family_counts[family] = family_counts.get(family, 0) + 1
            if len(result) >= limit:
                break
        return result

    def _merge_deals(self, first: list[SteamDeal], second: list[SteamDeal]) -> list[SteamDeal]:
        merged: dict[int, SteamDeal] = {}
        for deal in [*first, *second]:
            existing = merged.get(deal.appid)
            if existing is None:
                merged[deal.appid] = deal
                continue
            existing.categories.update(deal.categories)
            if not existing.image_url:
                existing.image_url = deal.image_url
            if not existing.released:
                existing.released = deal.released
            if not existing.review_summary:
                existing.review_summary = deal.review_summary
            if not existing.review_count and deal.review_count:
                existing.review_count = deal.review_count
            if not existing.review_percent and deal.review_percent:
                existing.review_percent = deal.review_percent
            if not existing.source and deal.source:
                existing.source = deal.source
            if deal.promotion_kind:
                existing.promotion_kind = deal.promotion_kind
                existing.promotion_start = deal.promotion_start
                existing.promotion_end = deal.promotion_end
                existing.final_price_cents = min(existing.final_price_cents, deal.final_price_cents)
                existing.categories.update(deal.categories)
            if deal.market_rank and (not existing.market_rank or deal.market_rank < existing.market_rank):
                existing.market_rank = deal.market_rank
        return list(merged.values())

    def _parse_featured_categories(self, data: Any) -> list[SteamDeal]:
        if not isinstance(data, dict):
            raise SteamDealsError("Steam 商店返回格式异常")

        specials = data.get("specials")
        items = specials.get("items") if isinstance(specials, dict) else None
        if not isinstance(items, list):
            raise SteamDealsError("Steam 商店特惠列表格式异常")

        parsed: list[SteamDeal] = []
        seen: set[int] = set()
        for item in items:
            deal = _parse_deal_item(item)
            if deal is None or deal.appid in seen:
                continue
            seen.add(deal.appid)
            parsed.append(deal)
        return parsed


def _parse_deal_item(item: Any) -> SteamDeal | None:
    if not isinstance(item, dict):
        return None
    appid = _safe_int(item.get("id") or item.get("appid"))
    name = str(item.get("name") or "").strip()
    if appid <= 0 or not name:
        return None
    if item.get("type") not in (None, 0, "0", "app"):
        return None

    final_price = _safe_int(item.get("final_price") or item.get("discounted_price"))
    original_price = _safe_int(item.get("original_price") or item.get("initial_price") or final_price)
    discount = _safe_int(item.get("discount_percent"))
    currency = str(item.get("currency") or "").strip()
    image_url = str(
        item.get("large_capsule_image")
        or item.get("small_capsule_image")
        or item.get("header_image")
        or ""
    ).strip()

    return SteamDeal(
        appid=appid,
        name=name,
        url=f"https://store.steampowered.com/app/{appid}/",
        image_url=image_url,
        discount_percent=max(0, min(discount, 100)),
        original_price_cents=max(0, original_price),
        final_price_cents=max(0, final_price),
        currency=currency,
        source="精选",
    )


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class _SearchResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.deals: list[SteamDeal] = []
        self.current: dict[str, Any] | None = None
        self.depth = 0
        self.capture: str | None = None
        self.buffer: list[str] = []

    def parse(self, html: str) -> list[SteamDeal]:
        self.feed(html)
        return self.deals

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        classes = set(str(attr.get("class") or "").split())

        if tag == "a" and "search_result_row" in classes:
            self.current = {
                "appid": _safe_int(attr.get("data-ds-appid")),
                "href": str(attr.get("href") or "").strip(),
            }
            self.depth = 1
            return

        if self.current is None:
            return
        self.depth += 1

        if tag == "img" and not self.current.get("image_url"):
            self.current["image_url"] = str(attr.get("src") or "").strip()
        if tag == "div" and "search_price_discount_combined" in classes:
            self.current["final_price_cents"] = _safe_int(attr.get("data-price-final"))
        if tag == "span" and "search_review_summary" in classes:
            tooltip = str(attr.get("data-tooltip-html") or "")
            summary, percent, count = _parse_review_details(tooltip)
            self.current["review_summary"] = summary
            self.current["review_percent"] = percent
            self.current["review_count"] = count

        capture_by_class = {
            "title": "name",
            "search_released": "released",
            "discount_pct": "discount_percent_text",
            "discount_original_price": "original_price_text",
            "discount_final_price": "final_price_text",
        }
        for class_name, field in capture_by_class.items():
            if class_name in classes:
                self.capture = field
                self.buffer = []
                break

    def handle_endtag(self, tag: str) -> None:
        if self.current is None:
            return

        if self.capture is not None:
            text = _normalize_spaces("".join(self.buffer))
            if text:
                self.current[self.capture] = text
            self.capture = None
            self.buffer = []

        if tag == "a":
            deal = _parse_search_item(self.current)
            if deal is not None:
                self.deals.append(deal)
            self.current = None
            self.depth = 0
            return
        self.depth = max(0, self.depth - 1)

    def handle_data(self, data: str) -> None:
        if self.current is not None and self.capture is not None:
            self.buffer.append(data)


def _parse_search_item(item: dict[str, Any]) -> SteamDeal | None:
    appid = _safe_int(item.get("appid"))
    name = str(item.get("name") or "").strip()
    if appid <= 0 or not name:
        return None

    final_price = _safe_int(item.get("final_price_cents"))
    if final_price <= 0 and str(item.get("final_price_text") or "").strip() != "免费":
        final_price = _parse_price_cents(str(item.get("final_price_text") or ""))
    original_price = _parse_price_cents(str(item.get("original_price_text") or ""))
    if original_price <= 0:
        original_price = final_price
    discount = _parse_discount_percent(str(item.get("discount_percent_text") or ""))

    return SteamDeal(
        appid=appid,
        name=name,
        url=f"https://store.steampowered.com/app/{appid}/",
        image_url=str(item.get("image_url") or "").strip(),
        discount_percent=discount,
        original_price_cents=max(0, original_price),
        final_price_cents=max(0, final_price),
        currency="",
        source="搜索",
        released=str(item.get("released") or "").strip(),
        review_summary=str(item.get("review_summary") or "").strip(),
        review_percent=_safe_int(item.get("review_percent")),
        review_count=_safe_int(item.get("review_count")),
    )


class _SteamDbPromotionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, Any]] = []
        self.current: dict[str, Any] | None = None
        self.capture_text = False
        self.text_parts: list[str] = []
        self.capture_time: str | None = None

    def parse(self, html: str) -> list[SteamDeal]:
        self.feed(html)
        self.close()
        return [deal for row in self.rows if (deal := _parse_steamdb_row(row)) is not None]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        href = str(attr.get("href") or "")
        appid = _extract_appid(href)
        if tag == "a" and appid is not None:
            if self.current is not None:
                self._finish_current()
            self.current = {
                "appid": appid,
                "href": href,
            }
            self.capture_text = True
            self.text_parts = []
            return

        if self.current is None:
            return
        if tag == "time":
            datetime_value = str(attr.get("datetime") or attr.get("title") or "").strip()
            self.capture_time = datetime_value or "text"
            self.text_parts = []

    def handle_endtag(self, tag: str) -> None:
        if self.current is None:
            return
        if tag == "a" and self.capture_text:
            text = _normalize_spaces(" ".join(self.text_parts))
            if text:
                self.current["name"] = text
            self.capture_text = False
            self.text_parts = []
        elif tag == "time" and self.capture_time:
            text = _normalize_spaces(" ".join(self.text_parts))
            self.current.setdefault("times", []).append(self.capture_time if self.capture_time != "text" else text)
            self.capture_time = None
            self.text_parts = []

    def handle_data(self, data: str) -> None:
        if self.current is None:
            return
        text = _normalize_spaces(data)
        if not text:
            return
        if self.capture_text or self.capture_time:
            self.text_parts.append(text)
        blob = self.current.setdefault("blob", [])
        blob.append(text)
        if "Free to Keep" in text:
            self.current["promotion_kind"] = "free_to_keep"
        elif "Play For Free" in text:
            self.current["promotion_kind"] = "play_for_free"

    def _finish_current(self) -> None:
        if self.current is not None:
            self.rows.append(self.current)
        self.current = None
        self.capture_text = False
        self.capture_time = None
        self.text_parts = []

    def close(self) -> None:
        self._finish_current()
        super().close()


def _parse_steamdb_row(row: dict[str, Any]) -> SteamDeal | None:
    appid = _safe_int(row.get("appid"))
    name = _clean_steamdb_name(str(row.get("name") or ""))
    kind = str(row.get("promotion_kind") or _promotion_kind_from_blob(row.get("blob") or "")).strip()
    if appid <= 0 or not name or kind not in {"free_to_keep", "play_for_free"}:
        return None
    times = [str(item).strip() for item in row.get("times") or [] if str(item).strip()]
    return SteamDeal(
        appid=appid,
        name=name,
        url=f"https://store.steampowered.com/app/{appid}/",
        image_url=f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/capsule_231x87.jpg",
        discount_percent=100 if kind == "free_to_keep" else 0,
        original_price_cents=0,
        final_price_cents=0,
        currency="",
        source="SteamDB",
        promotion_kind=kind,
        promotion_start=times[0] if times else "",
        promotion_end=times[1] if len(times) > 1 else "",
    )


def _extract_appid(value: str) -> int | None:
    patterns = [
        r"store\.steampowered\.com/app/(\d+)",
        r"/app/(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, value)
        if match:
            return int(match.group(1))
    return None


def _clean_steamdb_name(value: str) -> str:
    text = _normalize_spaces(value)
    text = re.sub(r"^(?:View Store|Store|Install)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+(?:Free to Keep|Play For Free).*$", "", text, flags=re.IGNORECASE)
    return text.strip()


def _promotion_kind_from_blob(value: Any) -> str:
    text = " ".join(str(part) for part in value) if isinstance(value, list) else str(value)
    if "Free to Keep" in text:
        return "free_to_keep"
    if "Play For Free" in text:
        return "play_for_free"
    return ""


def _promotion_sort(deal: SteamDeal) -> int:
    if deal.promotion_kind == "free_to_keep":
        return 0
    if deal.promotion_kind == "play_for_free":
        return 1
    if deal.is_free:
        return 2
    return 3


def _market_source(filter_name: str) -> str:
    normalized = filter_name.strip().casefold()
    if normalized == "popularnew":
        return "热门"
    if normalized == "topsellers":
        return "热卖"
    return "榜单"


def _daily_rank(
    deal: SteamDeal,
    max_low_price: int,
    min_discount: int,
    min_reviews: int,
    min_low_discount: int,
    min_recent_discount: int,
    max_search_age_days: int,
    require_recent_search: bool,
) -> int:
    low = 0 < deal.final_price_cents <= max_low_price
    big_discount = deal.discount_percent >= min_discount
    recent = _is_recent_release(deal, max_search_age_days)
    recent_ok = recent or not require_recent_search
    if deal.promotion_kind == "free_to_keep":
        return 0
    if deal.promotion_kind == "play_for_free":
        return 1
    if _is_market_item(deal):
        return 2
    if deal.is_free:
        return 3
    if "新打折" in deal.categories:
        return 4
    if "折扣加深" in deal.categories:
        return 5
    if deal.source == "精选" and (low or big_discount):
        return 6
    if require_recent_search and deal.source == "搜索" and not recent:
        return 90
    if big_discount and recent_ok and deal.review_count >= min_reviews:
        return 7
    if big_discount and recent_ok:
        return 8
    if low and recent_ok and (deal.review_count >= min_reviews or deal.discount_percent >= min_low_discount):
        return 9
    if recent_ok and deal.discount_percent >= min_recent_discount and deal.review_count >= min_reviews:
        return 10
    if recent_ok and deal.discount_percent >= min_recent_discount:
        return 11
    return 90


def _daily_sort_key(
    deal: SteamDeal,
    max_low_price: int,
    min_discount: int,
    min_reviews: int,
    min_low_discount: int,
    min_recent_discount: int,
    max_search_age_days: int,
    require_recent_search: bool,
) -> tuple[int, int, int, int, int, int, int, str]:
    return (
        _daily_rank(
            deal,
            max_low_price,
            min_discount,
            min_reviews,
            min_low_discount,
            min_recent_discount,
            max_search_age_days,
            require_recent_search,
        ),
        deal.market_rank or 999999,
        0 if deal.source == "精选" else 1,
        -_release_ordinal(deal),
        -min(deal.review_count, 5000),
        -deal.discount_percent,
        deal.final_price_cents,
        deal.name.casefold(),
    )


def _is_plain_low_price(
    deal: SteamDeal,
    max_low_price: int,
    min_discount: int,
    min_reviews: int,
    min_low_discount: int,
) -> bool:
    low = 0 < deal.final_price_cents <= max_low_price
    big_discount = deal.discount_percent >= min_discount
    return low and not big_discount and deal.review_count < min_reviews and deal.discount_percent < min_low_discount


def _is_changed_discount(deal: SteamDeal) -> bool:
    return "新打折" in deal.categories or "折扣加深" in deal.categories


def _is_market_item(deal: SteamDeal) -> bool:
    return deal.source in {"热卖", "热门", "榜单"} or bool({"热卖", "热门", "榜单"} & deal.categories)


def _title_family(name: str) -> str:
    text = name.casefold()
    text = re.sub(r"[:：].*$", "", text)
    text = re.sub(r"\b(?:chapter|episode|part|vol|volume|season)\s*\d+\b", "", text)
    text = re.sub(r"\b(?:19|20)\d{2}\b", "", text)
    text = re.sub(r"\b\d+\b", "", text)
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", text).strip()
    parts = text.split()
    while parts and parts[0] in {"the", "a", "an"}:
        parts.pop(0)
    if not parts:
        return name.casefold()
    first = parts[0]
    if re.search(r"[\u4e00-\u9fff]", first):
        return first[:4]
    return first


def _is_recent_release(deal: SteamDeal, max_age_days: int) -> bool:
    released = _parse_release_date(deal.released)
    if released is None:
        return False
    return (date.today() - released).days <= max_age_days


def _release_ordinal(deal: SteamDeal) -> int:
    released = _parse_release_date(deal.released)
    return released.toordinal() if released is not None else 0


def _parse_release_date(value: str) -> date | None:
    text = _normalize_spaces(value)
    if not text:
        return None
    patterns = [
        r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
        r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    return None


def _parse_discount_percent(value: str) -> int:
    match = re.search(r"(\d+)", value)
    return max(0, min(int(match.group(1)), 100)) if match else 0


def _parse_price_cents(value: str) -> int:
    text = _normalize_spaces(value)
    if not text or text == "免费":
        return 0
    match = re.search(r"(\d+(?:[.,]\d+)?)", text.replace(",", "."))
    if not match:
        return 0
    try:
        return int(round(float(match.group(1)) * 100))
    except ValueError:
        return 0


def _parse_review_details(value: str) -> tuple[str, int, int]:
    summary = re.sub(r"<br\s*/?>.*", "", unescape(value), flags=re.IGNORECASE)
    text = _normalize_spaces(unescape(value))
    percent_match = re.search(r"(\d+)%", text)
    count_match = re.search(r"([\d,]+)\s*篇", text)
    if count_match is None:
        count_match = re.search(r"([\d,]+)\s+user reviews", text, re.IGNORECASE)
    percent = int(percent_match.group(1)) if percent_match else 0
    count = int(count_match.group(1).replace(",", "")) if count_match else 0
    return _normalize_spaces(summary), percent, count


def _normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value)).strip()
