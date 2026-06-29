from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx


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
    categories: set[str] = field(default_factory=set)

    @property
    def is_free(self) -> bool:
        return self.final_price_cents <= 0


_CACHE: dict[str, tuple[float, list[SteamDeal]]] = {}


class SteamDealsClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.api_url = str(config.get("api_url") or "").strip()
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
        cache_key = f"{self.api_url}|{self.country}|{self.language}"
        cached = _CACHE.get(cache_key)
        now = time.monotonic()
        if not force_refresh and cached and now - cached[0] < self.cache_ttl:
            return list(cached[1])

        try:
            async with httpx.AsyncClient(**self._client_kwargs()) as client:
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

        deals = self._parse_featured_categories(data)
        _CACHE[cache_key] = (now, deals)
        return list(deals)

    def filter_deals(self, deals: list[SteamDeal], mode: DealMode = "all") -> list[SteamDeal]:
        max_low_price = max(0, int(self.config.get("max_low_price_cents") or 1000))
        min_discount = max(0, min(int(self.config.get("min_discount_percent") or 90), 100))
        limit = max(1, int(self.config.get("max_items") or 12))

        filtered: list[SteamDeal] = []
        for deal in deals:
            free = deal.is_free
            low = 0 < deal.final_price_cents <= max_low_price
            big_discount = deal.discount_percent >= min_discount
            if free:
                deal.categories.add("免费")
            if low:
                deal.categories.add("低价")
            if big_discount:
                deal.categories.add("大折扣")

            if mode == "free" and free:
                filtered.append(deal)
            elif mode == "low" and not free and (low or big_discount):
                filtered.append(deal)
            elif mode == "all" and (free or low or big_discount):
                filtered.append(deal)

        filtered.sort(
            key=lambda item: (
                0 if item.is_free else 1,
                item.final_price_cents,
                -item.discount_percent,
                item.name.casefold(),
            )
        )
        return filtered[:limit]

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
    )


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
