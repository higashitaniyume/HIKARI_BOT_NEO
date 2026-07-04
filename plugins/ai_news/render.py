from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from core.bot_identity import get_bot_name
from core.rendering import load_font

from .ai_summary import AiDigestSummary
from .feed import NewsItem

BG = (247, 249, 252)
HEADER = (21, 32, 43)
INK = (29, 38, 50)
MUTED = (101, 116, 134)
SOFT = (226, 232, 240)
PANEL = (255, 255, 255)
ACCENTS = [
    (63, 131, 248),
    (16, 163, 127),
    (236, 137, 54),
    (188, 82, 226),
    (226, 73, 98),
    (78, 158, 182),
]


async def render_digest(
    items: list[NewsItem],
    *,
    config: dict[str, Any],
    generated_at: datetime | None = None,
    ai_summary: AiDigestSummary | None = None,
) -> Path:
    generated_at = generated_at or datetime.now(timezone.utc)
    cache_dir = Path(str(config.get("cache_dir") or "/tmp/hikari_bot/ai_news"))
    cache_dir.mkdir(parents=True, exist_ok=True)

    render_cfg = config.get("render") if isinstance(config.get("render"), dict) else {}
    width = 1180
    pad = 42
    header_h = 178
    row_gap = 16
    footer_h = 58
    summary_h = _summary_height(ai_summary) if ai_summary is not None else 0
    row_heights = [_row_height(item) for item in items] or [138]
    height = header_h + pad + summary_h + sum(row_heights) + row_gap * max(0, len(row_heights) - 1) + footer_h

    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)

    title_font = load_font(46, bold=True)
    subtitle_font = load_font(23)
    meta_font = load_font(19)
    item_title_font = load_font(27, bold=True)
    summary_font = load_font(20)
    badge_font = load_font(18, bold=True)
    small_font = load_font(17)
    summary_max_chars = _safe_int(config.get("summary_max_chars"), default=180, minimum=0, maximum=1000)

    draw.rectangle((0, 0, width, header_h), fill=HEADER)
    draw.rectangle((0, header_h - 5, width, header_h), fill=(72, 179, 157))
    draw.text((pad, 32), "AI 最新资讯", font=title_font, fill=(255, 255, 255))
    local_time = generated_at.astimezone().strftime("%Y-%m-%d %H:%M")
    mode = "AI 总结 + 翻译" if ai_summary is not None else "规则筛选"
    draw.text((pad, 94), f"{local_time} 生成 · {len(items)} 条精选 · {mode}", font=subtitle_font, fill=(207, 216, 226))
    subtitle = "来源按官方发布、研究社区与科技媒体综合排序"
    if ai_summary is not None and ai_summary.model_label:
        subtitle = f"{subtitle} · {ai_summary.model_label}"
    draw.text((pad, 130), subtitle, font=meta_font, fill=(162, 176, 194))

    y = header_h + pad
    if ai_summary is not None:
        y = _draw_summary_panel(draw, ai_summary, (pad, y, width - pad, y + summary_h - row_gap), title_font=load_font(28, bold=True), text_font=summary_font, small_font=small_font)
        y += row_gap

    if not items:
        _rounded_rect(draw, (pad, y, width - pad, y + 116), radius=8, fill=PANEL, outline=SOFT)
        draw.text((pad + 28, y + 38), "暂时没有筛到新的 AI 资讯。", font=item_title_font, fill=INK)
    for index, item in enumerate(items, start=1):
        row_h = row_heights[index - 1]
        x0, x1 = pad, width - pad
        y0, y1 = y, y + row_h
        _rounded_rect(draw, (x0, y0, x1, y1), radius=8, fill=PANEL, outline=SOFT)

        accent = _accent_for(item.source_id)
        draw.rectangle((x0, y0, x0 + 8, y1), fill=accent)
        rank = f"{index:02d}"
        rank_w, rank_h = _text_size(draw, rank, badge_font)
        draw.ellipse((x0 + 24, y0 + 24, x0 + 74, y0 + 74), fill=accent)
        draw.text((x0 + 24 + (50 - rank_w) / 2, y0 + 24 + (50 - rank_h) / 2 - 1), rank, font=badge_font, fill=(255, 255, 255))

        text_x = x0 + 96
        source_text = f"{item.source_title} · {item.source_group}"
        if item.published is not None:
            source_text = f"{source_text} · {_format_time(item.published)}"
        draw.text((text_x, y0 + 24), source_text, font=small_font, fill=MUTED)

        title_lines = _wrap_text(draw, item.title or "未命名资讯", item_title_font, width=x1 - text_x - 38, max_lines=2)
        title_y = y0 + 52
        for line in title_lines:
            draw.text((text_x, title_y), line, font=item_title_font, fill=INK)
            title_y += 34

        summary = _clean_summary(item.summary, max_chars=summary_max_chars)
        if summary:
            summary_lines = _wrap_text(draw, summary, summary_font, width=x1 - text_x - 38, max_lines=2)
            summary_y = title_y + 6
            for line in summary_lines:
                draw.text((text_x, summary_y), line, font=summary_font, fill=(76, 88, 106))
                summary_y += 27

        y = y1 + row_gap

    footer = f"{get_bot_name()} · AI News Source"
    draw.text((pad, height - 40), footer, font=small_font, fill=MUTED)

    image_format = str(render_cfg.get("image_format") or "PNG").strip().upper()
    suffix = ".jpg" if image_format in {"JPEG", "JPG"} else ".png"
    digest = hashlib.sha1("|".join(item.key for item in items).encode("utf-8")).hexdigest()[:10]
    output = cache_dir / f"ai_news_{generated_at.strftime('%Y%m%d_%H%M%S')}_{digest}{suffix}"
    if image_format in {"JPEG", "JPG"}:
        quality = max(50, min(int(render_cfg.get("jpeg_quality") or 86), 95))
        image.save(output, "JPEG", quality=quality, optimize=True, progressive=True)
    else:
        image.save(output, "PNG", optimize=True)
    return output


def _row_height(item: NewsItem) -> int:
    return 178 if item.summary else 142


def _summary_height(summary: AiDigestSummary | None) -> int:
    if summary is None:
        return 0
    bullet_count = max(1, min(len(summary.bullets), 4))
    return 96 + bullet_count * 34 + 16


def _draw_summary_panel(
    draw: ImageDraw.ImageDraw,
    summary: AiDigestSummary,
    box: tuple[int, int, int, int],
    *,
    title_font,
    text_font,
    small_font,
) -> int:
    x0, y0, x1, y1 = box
    _rounded_rect(draw, box, radius=8, fill=(235, 244, 250), outline=(197, 215, 228))
    draw.text((x0 + 28, y0 + 22), summary.title or "AI 摘要", font=title_font, fill=INK)
    bullets = summary.bullets or ["已根据当前资讯生成中文摘要。"]
    y = y0 + 66
    for bullet in bullets[:4]:
        draw.ellipse((x0 + 32, y + 8, x0 + 42, y + 18), fill=(72, 179, 157))
        lines = _wrap_text(draw, bullet, text_font, width=x1 - x0 - 96, max_lines=1)
        draw.text((x0 + 54, y), lines[0], font=text_font, fill=(46, 61, 78))
        y += 34
    if summary.model_label:
        label = f"由 {summary.model_label} 生成"
        label_w, _ = _text_size(draw, label, small_font)
        draw.text((x1 - 28 - label_w, y1 - 32), label, font=small_font, fill=MUTED)
    return y1


def _format_time(value: datetime) -> str:
    return value.astimezone().strftime("%m-%d %H:%M")


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
    units: list[str] = []
    for token in re.findall(r"[\u4e00-\u9fff]|[^\s\u4e00-\u9fff]+", str(text or "")):
        units.append(token)
    return units


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


def _accent_for(value: str) -> tuple[int, int, int]:
    digest = hashlib.sha1(str(value).encode("utf-8")).digest()[0]
    return ACCENTS[digest % len(ACCENTS)]


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
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _safe_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return min(max(parsed, minimum), maximum)
