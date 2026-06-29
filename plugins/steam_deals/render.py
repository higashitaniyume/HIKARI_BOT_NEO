from __future__ import annotations

import hashlib
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageOps

from core.rendering import load_font

from .api import SteamDeal

BG = (245, 247, 250)
INK = (28, 32, 40)
MUTED = (91, 101, 117)
CARD = (255, 255, 255)
LINE = (221, 226, 234)
STEAM = (23, 29, 37)
ACCENT = (18, 126, 177)
FREE = (35, 142, 75)
LOW = (184, 93, 12)
DISCOUNT = (73, 92, 174)


async def render_report(
    deals: list[SteamDeal],
    *,
    mode: str,
    config: dict[str, Any],
    generated_at: datetime | None = None,
) -> Path:
    generated_at = generated_at or datetime.now()
    cache_dir = Path(str(config.get("cache_dir") or "/tmp/hikari_bot/steam_deals"))
    cache_dir.mkdir(parents=True, exist_ok=True)

    cover_paths: dict[int, Path] = {}
    render_cfg = config.get("render") or {}
    if render_cfg.get("download_covers", True):
        cover_paths = await _download_covers(
            deals,
            cache_dir / "covers",
            proxy=str(config.get("proxy") or "").strip() or None,
            timeout=float(render_cfg.get("cover_timeout") or 10),
        )

    width = 1120
    header_h = 176
    row_h = 154
    pad = 34
    footer_h = 58
    height = header_h + max(1, len(deals)) * row_h + footer_h + pad

    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)
    title_font = load_font(44, bold=True)
    subtitle_font = load_font(23)
    meta_font = load_font(19)
    name_font = load_font(28, bold=True)
    price_font = load_font(25, bold=True)
    small_font = load_font(18)

    draw.rectangle((0, 0, width, header_h), fill=STEAM)
    draw.text((pad, 32), _title_for_mode(mode), font=title_font, fill=(255, 255, 255))
    subtitle = generated_at.strftime("%Y-%m-%d %H:%M")
    draw.text((pad, 96), f"Steam 官方特惠 · {subtitle}", font=subtitle_font, fill=(199, 211, 225))
    summary = _summary(deals, config)
    draw.text((pad, 133), summary, font=meta_font, fill=(152, 197, 227))

    y = header_h + 22
    if not deals:
        _rounded_rect(draw, (pad, y, width - pad, y + 112), radius=8, fill=CARD, outline=LINE)
        draw.text((pad + 28, y + 34), "今天暂时没有筛到符合条件的游戏。", font=name_font, fill=INK)
    for index, deal in enumerate(deals, start=1):
        x0 = pad
        x1 = width - pad
        y0 = y
        y1 = y + row_h - 18
        _rounded_rect(draw, (x0, y0, x1, y1), radius=8, fill=CARD, outline=LINE)

        cover_box = (x0 + 20, y0 + 24, x0 + 250, y0 + 110)
        _draw_cover(image, draw, cover_box, cover_paths.get(deal.appid))

        text_x = x0 + 276
        name = f"{index}. {deal.name}"
        draw.text((text_x, y0 + 22), _ellipsize(draw, name, name_font, 510), font=name_font, fill=INK)
        draw.text((text_x, y0 + 60), f"AppID {deal.appid}", font=small_font, fill=MUTED)
        _draw_tags(draw, text_x, y0 + 88, deal.categories, small_font)

        price_text = _price_text(deal, config)
        price_w = _text_size(draw, price_text, price_font)[0]
        draw.text((x1 - 32 - price_w, y0 + 30), price_text, font=price_font, fill=_price_color(deal))
        if deal.discount_percent > 0:
            discount_text = f"-{deal.discount_percent}%"
            discount_w = _text_size(draw, discount_text, small_font)[0]
            _rounded_rect(
                draw,
                (x1 - 32 - discount_w - 18, y0 + 72, x1 - 32, y0 + 101),
                radius=6,
                fill=(228, 240, 255),
            )
            draw.text((x1 - 32 - discount_w - 9, y0 + 77), discount_text, font=small_font, fill=DISCOUNT)

        y += row_h

    footer = "数据来自 Steam Store featuredcategories API，限免领取活动可能需要后续数据源增强。"
    draw.text((pad, height - 42), footer, font=small_font, fill=MUTED)

    filename = f"steam_deals_{generated_at.strftime('%Y%m%d_%H%M%S')}_{mode}.png"
    output = cache_dir / filename
    image.save(output, "PNG", optimize=True)
    return output


async def _download_covers(
    deals: list[SteamDeal],
    cache_dir: Path,
    *,
    proxy: str | None,
    timeout: float,
) -> dict[int, Path]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    result: dict[int, Path] = {}
    kwargs: dict[str, Any] = {
        "timeout": httpx.Timeout(timeout, connect=min(timeout, 5.0)),
        "follow_redirects": True,
        "headers": {"User-Agent": "HIKARI_BOT_NEO steam_deals"},
    }
    if proxy:
        kwargs["proxy"] = proxy

    async with httpx.AsyncClient(**kwargs) as client:
        for deal in deals:
            if not deal.image_url:
                continue
            suffix = ".jpg"
            digest = hashlib.sha1(deal.image_url.encode("utf-8")).hexdigest()[:16]
            path = cache_dir / f"{deal.appid}_{digest}{suffix}"
            if not path.exists():
                try:
                    response = await client.get(deal.image_url)
                    response.raise_for_status()
                    path.write_bytes(response.content)
                except Exception:
                    continue
            result[deal.appid] = path
    return result


def _draw_cover(image: Image.Image, draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], path: Path | None) -> None:
    _rounded_rect(draw, box, radius=6, fill=(226, 232, 239))
    if path is None or not path.exists():
        draw.text((box[0] + 52, box[1] + 30), "STEAM", font=load_font(22, bold=True), fill=MUTED)
        return
    try:
        cover = Image.open(path).convert("RGB")
    except Exception:
        return
    cover = ImageOps.fit(cover, (box[2] - box[0], box[3] - box[1]), method=Image.Resampling.LANCZOS)
    image.paste(cover, (box[0], box[1]))


def _draw_tags(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    categories: set[str],
    font,
) -> None:
    colors = {"免费": FREE, "低价": LOW, "大折扣": DISCOUNT}
    current_x = x
    for tag in sorted(categories):
        color = colors.get(tag, ACCENT)
        text_w, text_h = _text_size(draw, tag, font)
        rect = (current_x, y, current_x + text_w + 20, y + text_h + 10)
        _rounded_rect(draw, rect, radius=5, fill=color)
        draw.text((current_x + 10, y + 5), tag, font=font, fill=(255, 255, 255))
        current_x = rect[2] + 8


def _rounded_rect(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    *,
    radius: int,
    fill,
    outline=None,
) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline)


def _price_text(deal: SteamDeal, config: dict[str, Any]) -> str:
    if deal.is_free:
        return "免费"
    symbol = str(config.get("currency_symbol") or "¥")
    value = deal.final_price_cents / 100
    return f"{symbol}{value:.2f}".rstrip("0").rstrip(".")


def _price_color(deal: SteamDeal) -> tuple[int, int, int]:
    if deal.is_free:
        return FREE
    if deal.final_price_cents <= 1000:
        return LOW
    return ACCENT


def _summary(deals: list[SteamDeal], config: dict[str, Any]) -> str:
    free_count = sum(1 for item in deals if item.is_free)
    low_count = sum(1 for item in deals if 0 < item.final_price_cents <= int(config.get("max_low_price_cents") or 1000))
    big_count = sum(1 for item in deals if item.discount_percent >= int(config.get("min_discount_percent") or 90))
    return f"{len(deals)} 款入选 · 免费 {free_count} · 低价 {low_count} · 大折扣 {big_count}"


def _title_for_mode(mode: str) -> str:
    if mode == "free":
        return "Steam 免费游戏日报"
    if mode == "low":
        return "Steam 低价游戏日报"
    return "Steam 喜加一游戏日报"


def _ellipsize(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    if _text_size(draw, text, font)[0] <= max_width:
        return text
    suffix = "..."
    suffix_w = _text_size(draw, suffix, font)[0]
    keep = max(1, math.floor(len(text) * max_width / max(_text_size(draw, text, font)[0], 1)))
    candidate = text[:keep].rstrip()
    while candidate and _text_size(draw, candidate, font)[0] + suffix_w > max_width:
        candidate = candidate[:-1].rstrip()
    return f"{candidate}{suffix}" if candidate else suffix


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0], box[3] - box[1]
