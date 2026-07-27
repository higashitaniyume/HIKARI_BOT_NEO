"""
贴纸触发插件工具函数模块。

处理拼图生成、预览图渲染、文字换行等离线计算逻辑。
"""

from __future__ import annotations

import hashlib
import logging
import math
import shutil
import time
from pathlib import Path

from core.rendering import draw_text, load_font, text_size
from plugins import sticker_library

logger = logging.getLogger("HikariBot.StickerPlugin")

SHARED_DIR = Path("/tmp/hikari_bot/stickers")
PACK_PREVIEW_LIMIT = 6
PACK_PREVIEW_IMAGE_WIDTH = 1200


def _cleanup_shared_dir():
    """删除超过 2 分钟的临时贴纸文件，避免堆积。"""
    if not SHARED_DIR.is_dir():
        return
    now = time.time()
    removed = 0
    for f in SHARED_DIR.iterdir():
        if f.is_file() and now - f.stat().st_mtime > 120:
            f.unlink(missing_ok=True)
            removed += 1
    if removed:
        logger.debug(f"[Sticker] 清理临时文件 {removed} 个")


def _copy_to_shared(source: Path) -> Path:
    """将表情包复制到 NapCat 可读的共享目录。"""
    SHARED_DIR.mkdir(parents=True, exist_ok=True)

    # 用完整路径哈希，避免不同贴纸包同名文件碰撞
    path_hash = hashlib.sha256(str(source.resolve()).encode()).hexdigest()[:16]
    dest = SHARED_DIR / f"{path_hash}{source.suffix}"

    if not dest.exists():
        shutil.copy2(source, dest)
        logger.debug(f"[Sticker] 已复制到共享目录 → {dest}")

    return dest


def _safe_output_label(value: str) -> str:
    import re
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", value).strip(" ._")
    return value[:48] or "stickers"


async def _make_collage(files: list[Path], folder_name: str) -> Path:
    """将所有图片的第一帧拼成尽可能正方形的网格图。

    使用线程池执行 PIL 操作，避免阻塞事件循环。
    """
    import asyncio
    from PIL import Image

    THUMB_SIZE = 200  # 每格缩略图尺寸

    def _do_collage() -> Path:
        images: list[Image.Image] = []
        for f in sorted(files):
            try:
                img = Image.open(f)
                # GIF/动画取第一帧
                if getattr(img, "is_animated", False):
                    img.seek(0)
                # 统一转 RGB
                if img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGBA")
                # 缩放到统一尺寸（保持比例，填白）
                img.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.Resampling.LANCZOS)
                bg = Image.new("RGBA", (THUMB_SIZE, THUMB_SIZE), (255, 255, 255, 0))
                ox = (THUMB_SIZE - img.width) // 2
                oy = (THUMB_SIZE - img.height) // 2
                bg.paste(img, (ox, oy), img if img.mode == "RGBA" else None)
                images.append(bg)
            except Exception as e:
                logger.warning(f"[Sticker] 拼图跳过 {f.name}: {e}")

        if not images:
            raise RuntimeError("没有可处理的图片")

        # 尽可能正方形
        n = len(images)
        cols = math.ceil(math.sqrt(n))
        rows = math.ceil(n / cols)

        canvas = Image.new("RGB", (cols * THUMB_SIZE, rows * THUMB_SIZE), (255, 255, 255))
        for i, img in enumerate(images):
            row = i // cols
            col = i % cols
            canvas.paste(img.convert("RGB"), (col * THUMB_SIZE, row * THUMB_SIZE))

        label = _safe_output_label(folder_name)
        out_path = SHARED_DIR / f"collage_{label}_{len(images)}.jpg"
        canvas.save(out_path, "JPEG", quality=85)
        return out_path

    return await asyncio.to_thread(_do_collage)


def _text_width(draw, text: str, font) -> int:
    return text_size(draw, text, font)[0]


def _line_height(draw, font) -> int:
    return max(1, text_size(draw, "Ag国", font)[1])


def _wrap_text(draw, text: str, font, max_width: int, max_lines: int) -> list[str]:
    text = str(text or "").strip()
    if not text:
        return [""]

    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if current and _text_width(draw, candidate, font) > max_width:
            lines.append(current)
            current = char
            if len(lines) >= max_lines:
                break
        else:
            current = candidate

    if len(lines) < max_lines and current:
        lines.append(current)

    if len(lines) > max_lines:
        lines = lines[:max_lines]

    if lines and _text_width(draw, lines[-1], font) > max_width:
        while lines[-1] and _text_width(draw, lines[-1] + "...", font) > max_width:
            lines[-1] = lines[-1][:-1]
        lines[-1] = lines[-1] + "..."
    elif current and len("".join(lines)) < len(text):
        while lines[-1] and _text_width(draw, lines[-1] + "...", font) > max_width:
            lines[-1] = lines[-1][:-1]
        lines[-1] = lines[-1] + "..."

    return lines or [""]


def _load_preview_frame(path: Path, size: int):
    from PIL import Image

    with Image.open(path) as img:
        if getattr(img, "is_animated", False):
            img.seek(0)
        frame = img.convert("RGBA")
        frame.thumbnail((size, size), Image.Resampling.LANCZOS)
        tile = Image.new("RGBA", (size, size), (255, 255, 255, 0))
        x = (size - frame.width) // 2
        y = (size - frame.height) // 2
        tile.alpha_composite(frame, (x, y))
        return tile


async def _make_pack_preview_image() -> Path:
    import asyncio
    from PIL import Image, ImageDraw

    from core.bot_messages import get_message as msg

    def _do_render() -> Path:
        state = sticker_library.get_state()
        packs = state.get("packs") or []
        if not packs:
            raise RuntimeError("暂无贴纸包。")

        width = PACK_PREVIEW_IMAGE_WIDTH
        margin = 36
        card_gap = 18
        card_padding = 22
        thumb_size = 132
        thumb_gap = 12
        title_font = load_font(34, bold=True)
        subtitle_font = load_font(22)
        name_font = load_font(28, bold=True)
        meta_font = load_font(18)
        keyword_font = load_font(20)
        scratch = Image.new("RGB", (width, 400), (255, 255, 255))
        draw = ImageDraw.Draw(scratch)

        preview_area_width = thumb_size * 3 + thumb_gap * 2
        text_width = width - margin * 2 - card_padding * 2 - preview_area_width - 28
        rows: list[dict] = []
        for pack in packs:
            keywords = pack.get("keywords") or []
            keyword_text = msg(
                "sticker.preview_keyword",
                keywords="、".join(str(item) for item in keywords) if keywords else msg("sticker.no_keywords"),
            )
            title_lines = _wrap_text(draw, str(pack.get("name") or ""), name_font, text_width, 2)
            keyword_lines = _wrap_text(draw, keyword_text, keyword_font, text_width, 3)
            text_height = (
                len(title_lines) * (_line_height(draw, name_font) + 4)
                + 10
                + _line_height(draw, meta_font)
                + 12
                + len(keyword_lines) * (_line_height(draw, keyword_font) + 5)
            )
            preview_ids = [str(item) for item in pack.get("previews") or []][:PACK_PREVIEW_LIMIT]
            preview_paths = [
                path
                for sticker_id in preview_ids
                if (path := sticker_library.get_sticker_path(sticker_id)) is not None
            ]
            preview_rows = 2 if len(preview_paths) > 3 else 1
            preview_height = preview_rows * thumb_size + max(0, preview_rows - 1) * thumb_gap
            card_height = max(text_height, preview_height) + card_padding * 2
            rows.append({
                "pack": pack,
                "title_lines": title_lines,
                "keyword_lines": keyword_lines,
                "preview_paths": preview_paths,
                "height": card_height,
            })

        header_height = 118
        total_height = margin + header_height + sum(row["height"] for row in rows) + card_gap * (len(rows) - 1) + margin
        image = Image.new("RGB", (width, total_height), (246, 248, 245))
        draw = ImageDraw.Draw(image)

        y = margin
        draw_text(draw, (margin, y), msg("sticker.preview_title"), fill=(26, 33, 28), font=title_font)
        y += 48
        summary = msg(
            "sticker.preview_summary",
            pack_count=len(packs),
            sticker_count=state.get("total_stickers", 0),
            keyword_count=len(state.get("keywords") or []),
        )
        draw_text(draw, (margin, y), summary, fill=(92, 104, 96), font=subtitle_font)
        y = margin + header_height

        for row in rows:
            pack = row["pack"]
            card_x = margin
            card_y = y
            card_w = width - margin * 2
            card_h = row["height"]
            draw.rounded_rectangle(
                (card_x, card_y, card_x + card_w, card_y + card_h),
                radius=18,
                fill=(255, 255, 255),
                outline=(220, 228, 220),
                width=2,
            )

            text_x = card_x + card_padding
            text_y = card_y + card_padding
            for line in row["title_lines"]:
                draw_text(draw, (text_x, text_y), line, fill=(24, 32, 27), font=name_font)
                text_y += _line_height(draw, name_font) + 4
            text_y += 8
            draw_text(
                draw,
                (text_x, text_y),
                msg("sticker.preview_pack_count", count=pack.get("count", 0)),
                fill=(92, 104, 96),
                font=meta_font,
            )
            text_y += _line_height(draw, meta_font) + 12
            for line in row["keyword_lines"]:
                draw_text(draw, (text_x, text_y), line, fill=(54, 68, 58), font=keyword_font)
                text_y += _line_height(draw, keyword_font) + 5

            preview_x = card_x + card_w - card_padding - preview_area_width
            preview_y = card_y + (card_h - (thumb_size * 2 + thumb_gap)) // 2
            preview_y = max(card_y + card_padding, preview_y)
            for index, path in enumerate(row["preview_paths"]):
                col = index % 3
                line = index // 3
                tile_x = preview_x + col * (thumb_size + thumb_gap)
                tile_y = preview_y + line * (thumb_size + thumb_gap)
                draw.rounded_rectangle(
                    (tile_x, tile_y, tile_x + thumb_size, tile_y + thumb_size),
                    radius=14,
                    fill=(246, 248, 245),
                    outline=(224, 231, 225),
                )
                try:
                    frame = _load_preview_frame(path, thumb_size - 16)
                    image.paste(frame.convert("RGB"), (tile_x + 8, tile_y + 8), frame)
                except Exception as e:
                    logger.warning("[Sticker] 贴纸包预览图加载失败: %s -> %s", path, e)

            if not row["preview_paths"]:
                empty_text = msg("sticker.preview_empty")
                tx = preview_x + (preview_area_width - _text_width(draw, empty_text, keyword_font)) // 2
                ty = card_y + card_h // 2 - _line_height(draw, keyword_font) // 2
                draw_text(draw, (tx, ty), empty_text, fill=(139, 149, 140), font=keyword_font)

            y += card_h + card_gap

        SHARED_DIR.mkdir(parents=True, exist_ok=True)
        out_path = SHARED_DIR / f"pack_preview_{int(time.time())}.jpg"
        image.save(out_path, "JPEG", quality=80, optimize=True)
        return out_path

    return await asyncio.to_thread(_do_render)
