from __future__ import annotations

import re
from datetime import date
from html import unescape
from html.parser import HTMLParser
from typing import Any

from .api import SteamDeal, _safe_int


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
    text = re.sub(r"[^0-9a-z一-鿿]+", " ", text).strip()
    parts = text.split()
    while parts and parts[0] in {"the", "a", "an"}:
        parts.pop(0)
    if not parts:
        return name.casefold()
    first = parts[0]
    if re.search(r"[一-鿿]", first):
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
