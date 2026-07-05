from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from core.bot_identity import get_bot_name
from core.rendering import draw_text, load_font, text_size

from .api import ZhihuHotItem

BG = (245, 247, 250)
HEADER = (24, 32, 43)
INK = (26, 34, 45)
MUTED = (98, 111, 130)
SOFT = (223, 229, 237)
PANEL = (255, 255, 255)
ZHIHU_BLUE = (5, 109, 232)
WARM = (245, 164, 69)
GREEN = (36, 154, 120)


@dataclass(slots=True)
class _ItemLayout:
    title_lines: list[str]
    excerpt_lines: list[str]
    height: int


async def render_hot_list(
    items: list[ZhihuHotItem],
    *,
    config: dict[str, Any],
    generated_at: datetime | None = None,
) -> Path:
    generated_at = generated_at or datetime.now(timezone.utc)
    cache_dir = Path(str(config.get("cache_dir") or "/tmp/hikari_bot/zhihu_hot"))
    cache_dir.mkdir(parents=True, exist_ok=True)

    render_cfg = config.get("render") if isinstance(config.get("render"), dict) else {}
    width = 1180
    pad = 42
    header_h = 168
    row_gap = 16
    footer_h = 58

    title_font = load_font(46, bold=True)
    subtitle_font = load_font(23)
    meta_font = load_font(18)
    item_title_font = load_font(27, bold=True)
    summary_font = load_font(20)
    badge_font = load_font(18, bold=True)
    rank_font = load_font(22, bold=True)
    small_font = load_font(17)

    temp_image = Image.new("RGB", (1, 1), BG)
    temp_draw = ImageDraw.Draw(temp_image)
    text_width = width - pad * 2 - 96 - 222
    summary_max_chars = _safe_int(config.get("summary_max_chars"), default=150, minimum=0, maximum=1000)
    layouts = [_layout_item(temp_draw, item, item_title_font, summary_font, text_width, summary_max_chars) for item in items]
    row_heights = [layout.height for layout in layouts] or [126]
    height = header_h + pad + sum(row_heights) + row_gap * max(0, len(row_heights) - 1) + footer_h

    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)

    draw.rectangle((0, 0, width, header_h), fill=HEADER)
    draw.rectangle((0, header_h - 5, width, header_h), fill=ZHIHU_BLUE)
    draw_text(draw, (pad, 32), "知乎热搜", font=title_font, fill=(255, 255, 255))
    local_time = generated_at.astimezone().strftime("%Y-%m-%d %H:%M")
    draw_text(draw, (pad, 94), f"{local_time} 生成 · {len(items)} 条热榜问题", font=subtitle_font, fill=(210, 219, 230))
    draw_text(draw, (pad, 130), "来源：知乎 Topstory Hot List · 默认以图片形式推送", font=meta_font, fill=(164, 178, 196))
    _draw_header_mark(draw, (width - pad - 122, 36, width - pad, 118), title_font=load_font(32, bold=True))

    y = header_h + pad
    if not items:
        _rounded_rect(draw, (pad, y, width - pad, y + 116), radius=8, fill=PANEL, outline=SOFT)
        draw_text(draw, (pad + 28, y + 38), "暂时没有读取到知乎热搜。", font=item_title_font, fill=INK)

    for item, layout in zip(items, layouts, strict=False):
        x0, x1 = pad, width - pad
        y0, y1 = y, y + layout.height
        _rounded_rect(draw, (x0, y0, x1, y1), radius=8, fill=PANEL, outline=SOFT)
        accent = _accent_for(item.rank)
        draw.rectangle((x0, y0, x0 + 8, y1), fill=accent)

        rank_label = f"{item.rank:02d}"
        draw.ellipse((x0 + 24, y0 + 24, x0 + 74, y0 + 74), fill=accent)
        rank_w, rank_h = _text_size(draw, rank_label, rank_font)
        draw_text(draw, (x0 + 24 + (50 - rank_w) / 2, y0 + 24 + (50 - rank_h) / 2 - 1), rank_label, font=rank_font, fill=(255, 255, 255))

        text_x = x0 + 96
        meta = _item_meta(item)
        draw_text(draw, (text_x, y0 + 22), meta, font=small_font, fill=MUTED)
        if item.heat:
            _draw_pill(draw, item.heat, (x1 - 194, y0 + 20), badge_font)

        title_y = y0 + 52
        for line in layout.title_lines:
            draw_text(draw, (text_x, title_y), line, font=item_title_font, fill=INK)
            title_y += 34

        if layout.excerpt_lines:
            summary_y = title_y + 7
            for line in layout.excerpt_lines:
                draw_text(draw, (text_x, summary_y), line, font=summary_font, fill=(70, 82, 100))
                summary_y += 27

        y = y1 + row_gap

    footer = f"{get_bot_name()} · Zhihu Hot Source"
    draw_text(draw, (pad, height - 40), footer, font=small_font, fill=MUTED)

    image_format = str(render_cfg.get("image_format") or "PNG").strip().upper()
    suffix = ".jpg" if image_format in {"JPEG", "JPG"} else ".png"
    digest = hashlib.sha1("|".join(item.key for item in items).encode("utf-8")).hexdigest()[:10]
    output = cache_dir / f"zhihu_hot_{generated_at.strftime('%Y%m%d_%H%M%S')}_{digest}{suffix}"
    if image_format in {"JPEG", "JPG"}:
        quality = max(50, min(int(render_cfg.get("jpeg_quality") or 86), 95))
        image.save(output, "JPEG", quality=quality, optimize=True, progressive=True)
    else:
        image.save(output, "PNG", optimize=True)
    return output


def _layout_item(
    draw: ImageDraw.ImageDraw,
    item: ZhihuHotItem,
    title_font,
    summary_font,
    text_width: int,
    summary_max_chars: int,
) -> _ItemLayout:
    title_lines = _wrap_text(draw, item.title or "未命名问题", title_font, width=text_width, max_lines=2)
    excerpt = _clean_summary(item.excerpt, max_chars=summary_max_chars)
    excerpt_lines = _wrap_text(draw, excerpt, summary_font, width=text_width, max_lines=2) if excerpt else []
    height = 52 + len(title_lines) * 34 + (7 + len(excerpt_lines) * 27 if excerpt_lines else 0) + 26
    return _ItemLayout(title_lines=title_lines, excerpt_lines=excerpt_lines, height=max(138, height))


def _item_meta(item: ZhihuHotItem) -> str:
    parts = ["知乎热榜"]
    if item.answer_count > 0:
        parts.append(f"{item.answer_count} 回答")
    if item.follower_count > 0:
        parts.append(f"{item.follower_count} 关注")
    if item.debut:
        parts.append("新上榜")
    return " · ".join(parts)


def _draw_header_mark(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], *, title_font) -> None:
    x0, y0, x1, y1 = box
    _rounded_rect(draw, box, radius=8, fill=(35, 47, 62), outline=(60, 76, 98))
    draw.rectangle((x0, y1 - 8, x1, y1), fill=ZHIHU_BLUE)
    label = "知"
    label_w, label_h = _text_size(draw, label, title_font)
    draw_text(draw, (x0 + (x1 - x0 - label_w) / 2, y0 + (y1 - y0 - label_h) / 2 - 3), label, font=title_font, fill=(255, 255, 255))


def _draw_pill(draw: ImageDraw.ImageDraw, text: str, origin: tuple[int, int], font) -> None:
    x, y = origin
    w, h = _text_size(draw, text, font)
    box = (x, y, x + max(148, w + 30), y + 36)
    _rounded_rect(draw, box, radius=8, fill=(238, 246, 255), outline=(191, 216, 248))
    draw_text(draw, (box[0] + (box[2] - box[0] - w) / 2, y + (36 - h) / 2 - 1), text, font=font, fill=ZHIHU_BLUE)


def _clean_summary(value: str, *, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:max_chars]


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, *, width: int, max_lines: int) -> list[str]:
    units = _text_units(text)
    lines: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}{unit}" if _is_cjk_unit(unit) or not current else f"{current} {unit}"
        if _text_size(draw, candidate, font)[0] <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = unit
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    if len(lines) == max_lines and units:
        lines[-1] = _ellipsize(draw, lines[-1], font, width)
    return lines or [""]


def _text_units(text: str) -> list[str]:
    return re.findall(r"[\u4e00-\u9fff]|[^\s\u4e00-\u9fff]+", str(text or ""))


def _is_cjk_unit(value: str) -> bool:
    return len(value) == 1 and "\u4e00" <= value <= "\u9fff"


def _ellipsize(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
    if _text_size(draw, text, font)[0] <= width:
        return text
    suffix = "..."
    current = text
    while current and _text_size(draw, f"{current}{suffix}", font)[0] > width:
        current = current[:-1]
    return f"{current}{suffix}" if current else suffix


def _accent_for(rank: int) -> tuple[int, int, int]:
    if rank <= 3:
        return WARM
    if rank <= 10:
        return ZHIHU_BLUE
    return GREEN


def _rounded_rect(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    *,
    radius: int,
    fill,
    outline=None,
) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline)


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    return text_size(draw, text, font)


def _safe_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return min(max(parsed, minimum), maximum)
