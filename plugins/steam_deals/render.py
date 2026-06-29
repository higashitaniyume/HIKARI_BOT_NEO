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

BG = (16, 28, 39)
PANEL = (27, 40, 55)
PANEL_ALT = (37, 54, 72)
INK = (232, 239, 247)
MUTED = (135, 160, 181)
LINE = (55, 78, 100)
STEAM = (12, 19, 28)
ACCENT = (102, 192, 244)
FREE = (105, 185, 75)
LOW = (190, 226, 93)
DISCOUNT = (76, 107, 34)
PRICE_BG = (9, 17, 24)


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

    width = 1160
    header_h = 184
    row_h = 172
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
    draw.rectangle((0, header_h - 5, width, header_h), fill=ACCENT)
    draw.text((pad, 32), _title_for_mode(mode), font=title_font, fill=(255, 255, 255))
    subtitle = generated_at.strftime("%Y-%m-%d %H:%M")
    draw.text((pad, 96), f"Steam 热卖榜 + 官方特惠 + SteamDB 限免 · {subtitle}", font=subtitle_font, fill=(199, 211, 225))
    summary = _summary(deals, config)
    draw.text((pad, 133), summary, font=meta_font, fill=(152, 197, 227))

    y = header_h + 22
    if not deals:
        _rounded_rect(draw, (pad, y, width - pad, y + 112), radius=8, fill=PANEL, outline=LINE)
        draw.text((pad + 28, y + 34), "今天暂时没有筛到符合条件的游戏。", font=name_font, fill=INK)
    for index, deal in enumerate(deals, start=1):
        x0 = pad
        x1 = width - pad
        y0 = y
        y1 = y + row_h - 18
        _rounded_rect(draw, (x0, y0, x1, y1), radius=8, fill=PANEL if index % 2 else PANEL_ALT, outline=LINE)

        cover_box = (x0 + 18, y0 + 24, x0 + 249, y0 + 111)
        _draw_cover(image, draw, cover_box, cover_paths.get(deal.appid))

        text_x = x0 + 276
        name = f"{index}. {deal.name}"
        draw.text((text_x, y0 + 20), _ellipsize(draw, name, name_font, 530), font=name_font, fill=INK)
        draw.text((text_x, y0 + 58), _meta_line(deal), font=small_font, fill=MUTED)
        if deal.review_summary:
            draw.text((text_x, y0 + 84), _ellipsize(draw, deal.review_summary, small_font, 440), font=small_font, fill=(176, 214, 245))
        _draw_tags(draw, text_x, y0 + 113, deal.categories, small_font)

        _draw_price_panel(draw, (x1 - 260, y0 + 28, x1 - 24, y0 + 112), deal, config, price_font, small_font)

        y += row_h

    footer = "数据来自 Steam Store 与 SteamDB Free Promotions；新打折/折扣加深由本地价格快照辅助判断。"
    draw.text((pad, height - 42), footer, font=small_font, fill=MUTED)

    image_format = str(render_cfg.get("image_format") or "JPEG").strip().upper()
    suffix = ".jpg" if image_format in {"JPEG", "JPG"} else ".png"
    filename = f"steam_deals_{generated_at.strftime('%Y%m%d_%H%M%S')}_{mode}{suffix}"
    output = cache_dir / filename
    if image_format in {"JPEG", "JPG"}:
        quality = max(50, min(int(render_cfg.get("jpeg_quality") or 82), 95))
        image.save(output, "JPEG", quality=quality, optimize=True, progressive=True)
    else:
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
    _rounded_rect(draw, box, radius=6, fill=(42, 58, 73))
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
    colors = {
        "限免领取": (74, 160, 65),
        "免费试玩": (53, 112, 170),
        "热卖": (198, 134, 45),
        "热门": (180, 103, 43),
        "榜单": (146, 105, 62),
        "新打折": (198, 86, 43),
        "折扣加深": (167, 70, 118),
        "近期": (85, 154, 206),
        "免费": FREE,
        "低价": (93, 123, 37),
        "大折扣": DISCOUNT,
        "精选": (42, 96, 134),
        "搜索": (66, 82, 98),
        "SteamDB": (36, 76, 108),
    }
    current_x = x
    for tag in sorted(categories):
        color = colors.get(tag, ACCENT)
        text_w, text_h = _text_size(draw, tag, font)
        rect = (current_x, y, current_x + text_w + 20, y + text_h + 10)
        _rounded_rect(draw, rect, radius=5, fill=color)
        fill = (255, 255, 255) if tag != "低价" else (20, 30, 18)
        draw.text((current_x + 10, y + 5), tag, font=font, fill=fill)
        current_x = rect[2] + 8


def _draw_price_panel(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    deal: SteamDeal,
    config: dict[str, Any],
    price_font,
    small_font,
) -> None:
    x0, y0, x1, y1 = box
    if deal.discount_percent > 0:
        discount_box = (x0, y0, x0 + 82, y1)
        draw.rectangle(discount_box, fill=DISCOUNT)
        discount_text = f"-{deal.discount_percent}%"
        dw, dh = _text_size(draw, discount_text, price_font)
        draw.text((discount_box[0] + (82 - dw) // 2, y0 + (y1 - y0 - dh) // 2 - 2), discount_text, font=price_font, fill=LOW)
        price_x = x0 + 82
    else:
        price_x = x0

    draw.rectangle((price_x, y0, x1, y1), fill=PRICE_BG)
    price_text = _price_text(deal, config)
    price_w, _ = _text_size(draw, price_text, price_font)
    draw.text((x1 - 14 - price_w, y0 + 32), price_text, font=price_font, fill=_price_color(deal))
    original = _original_price_text(deal, config)
    if original:
        ow, oh = _text_size(draw, original, small_font)
        ox = x1 - 14 - ow
        oy = y0 + 12
        draw.text((ox, oy), original, font=small_font, fill=MUTED)
        draw.line((ox, oy + oh // 2 + 2, ox + ow, oy + oh // 2 + 2), fill=MUTED, width=2)


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


def _original_price_text(deal: SteamDeal, config: dict[str, Any]) -> str:
    if deal.original_price_cents <= deal.final_price_cents or deal.original_price_cents <= 0:
        return ""
    symbol = str(config.get("currency_symbol") or "¥")
    value = deal.original_price_cents / 100
    return f"{symbol}{value:.2f}".rstrip("0").rstrip(".")


def _price_color(deal: SteamDeal) -> tuple[int, int, int]:
    if deal.is_free:
        return FREE
    if deal.final_price_cents <= 1000:
        return LOW
    return ACCENT


def _meta_line(deal: SteamDeal) -> str:
    parts = [f"AppID {deal.appid}"]
    if deal.released:
        parts.append(deal.released)
    if deal.review_percent and deal.review_count:
        parts.append(f"好评 {deal.review_percent}%/{deal.review_count}")
    if deal.promotion_end:
        parts.append(f"截止 {deal.promotion_end}")
    if deal.original_price_cents > deal.final_price_cents and deal.original_price_cents:
        saved = (deal.original_price_cents - deal.final_price_cents) / 100
        parts.append(f"省 {saved:.2f}".rstrip("0").rstrip("."))
    return " · ".join(parts)


def _summary(deals: list[SteamDeal], config: dict[str, Any]) -> str:
    free_count = sum(1 for item in deals if item.is_free)
    low_count = sum(1 for item in deals if 0 < item.final_price_cents <= int(config.get("max_low_price_cents") or 1000))
    big_count = sum(1 for item in deals if item.discount_percent >= int(config.get("min_discount_percent") or 90))
    keep_count = sum(1 for item in deals if item.promotion_kind == "free_to_keep")
    trial_count = sum(1 for item in deals if item.promotion_kind == "play_for_free")
    new_count = sum(1 for item in deals if "新打折" in item.categories)
    deeper_count = sum(1 for item in deals if "折扣加深" in item.categories)
    market_count = sum(1 for item in deals if item.source in {"热卖", "热门", "榜单"} or bool({"热卖", "热门", "榜单"} & item.categories))
    return f"{len(deals)} 款入选 · 榜单 {market_count} · 新打折 {new_count} · 折扣加深 {deeper_count} · 限免领取 {keep_count} · 免费试玩 {trial_count} · 免费 {free_count} · 低价 {low_count} · 大折扣 {big_count}"


def _title_for_mode(mode: str) -> str:
    if mode == "free":
        return "Steam 免费游戏日报"
    if mode == "low":
        return "Steam 低价游戏日报"
    return "Steam 热门热卖日报"


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
