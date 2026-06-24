from __future__ import annotations

import hashlib
import math
import re
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageFilter

from core.rendering import load_font

from .api import mode_label

BG = (23, 25, 35)
PANEL = (34, 38, 53)
PANEL_2 = (43, 48, 65)
TEXT = (245, 247, 251)
MUTED = (173, 181, 197)
ACCENT = (255, 102, 170)
BLUE = (96, 165, 250)
GREEN = (82, 196, 129)
YELLOW = (245, 196, 83)


def _font(size: int, *, bold: bool = False):
    return load_font(size, bold=bold)


def _safe(value: Any, default: str = "-") -> str:
    if value is None or value == "":
        return default
    return str(value)


def _num(value: Any, digits: int = 0) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if digits <= 0:
        return f"{int(round(number)):,}"
    return f"{number:,.{digits}f}"


def _pct(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if number <= 1:
        number *= 100
    return f"{number:.2f}%"


def _duration(seconds: Any) -> str:
    try:
        value = int(seconds)
    except (TypeError, ValueError):
        return "-"
    hours = value // 3600
    if hours >= 24:
        return f"{hours // 24:,}d {hours % 24}h"
    return f"{hours:,}h"


def _rank_text(value: Any) -> str:
    text = _num(value)
    return f"#{text}" if text != "-" else "未上榜"


def _score_title(score: dict[str, Any]) -> tuple[str, str]:
    beatmap = score.get("beatmap") or {}
    beatmapset = score.get("beatmapset") or {}
    title = f"{beatmapset.get('artist') or '?'} - {beatmapset.get('title') or '?'}"
    version = beatmap.get("version") or ""
    return title, version


def _score_mods(score: dict[str, Any]) -> str:
    mods = score.get("mods") or []
    if isinstance(mods, list):
        return "".join(
            mod.get("acronym", str(mod)) if isinstance(mod, dict) else str(mod)
            for mod in mods
        )
    return str(mods)


def _date(value: Any) -> str:
    if not value:
        return "-"
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return str(value)[:10]


def _rounded(draw: ImageDraw.ImageDraw, xy, radius: int, fill, outline=None, width: int = 1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font,
    max_width: int,
    *,
    suffix: str = "...",
) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    result = text
    while result and draw.textlength(result + suffix, font=font) > max_width:
        result = result[:-1]
    return (result + suffix) if result else suffix


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, max_lines: int) -> list[str]:
    words = re.split(r"(\s+)", text)
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current}{word}"
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
            continue
        if current.strip():
            lines.append(current.strip())
        current = word.strip()
        if len(lines) >= max_lines:
            break
    if current.strip() and len(lines) < max_lines:
        lines.append(current.strip())
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    if lines and draw.textlength(lines[-1], font=font) > max_width:
        lines[-1] = _fit_text(draw, lines[-1], font, max_width)
    return lines or [""]


def _paste_circle(base: Image.Image, img: Image.Image, box: tuple[int, int, int, int]) -> None:
    size = (box[2] - box[0], box[3] - box[1])
    img = img.convert("RGB").resize(size, Image.Resampling.LANCZOS)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size[0] - 1, size[1] - 1), fill=255)
    base.paste(img, (box[0], box[1]), mask)


def _placeholder(size: tuple[int, int], color=PANEL_2) -> Image.Image:
    img = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(img)
    for y in range(size[1]):
        ratio = y / max(size[1] - 1, 1)
        r = int(color[0] * (1 - ratio) + ACCENT[0] * ratio * 0.35)
        g = int(color[1] * (1 - ratio) + ACCENT[1] * ratio * 0.35)
        b = int(color[2] * (1 - ratio) + ACCENT[2] * ratio * 0.35)
        draw.line((0, y, size[0], y), fill=(r, g, b))
    return img


async def fetch_image(
    url: str | None,
    cache_dir: Path,
    *,
    suffix: str = ".jpg",
    proxy: str = "",
) -> Image.Image | None:
    if not url:
        return None
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    path = cache_dir / f"{digest}{suffix}"
    if path.exists() and path.stat().st_size > 0:
        try:
            return Image.open(path).convert("RGB")
        except Exception:
            path.unlink(missing_ok=True)

    try:
        kwargs: dict[str, Any] = {"timeout": 15, "follow_redirects": True}
        if proxy:
            kwargs["proxy"] = proxy
        async with httpx.AsyncClient(**kwargs) as client:
            response = await client.get(url)
        response.raise_for_status()
        path.write_bytes(response.content)
        return Image.open(BytesIO(response.content)).convert("RGB")
    except Exception:
        return None


async def fetch_avatar(
    user: dict[str, Any],
    cache_dir: Path,
    *,
    proxy: str = "",
) -> Image.Image | None:
    avatar = await fetch_image(user.get("avatar_url"), cache_dir, suffix=".jpg", proxy=proxy)
    if avatar is not None:
        return avatar

    user_id = user.get("id")
    if user_id:
        return await fetch_image(
            f"https://a.ppy.sh/{user_id}",
            cache_dir,
            suffix=".jpg",
            proxy=proxy,
        )
    return None


class Canvas:
    def __init__(self, width: int, height: int) -> None:
        self.image = Image.new("RGB", (width, height), BG)
        self.draw = ImageDraw.Draw(self.image)
        self.width = width
        self.height = height

    def title(self, x: int, y: int, title: str, subtitle: str = "") -> None:
        self.draw.text((x, y), title, font=_font(34, bold=True), fill=TEXT)
        if subtitle:
            self.draw.text((x, y + 44), subtitle, font=_font(18), fill=MUTED)

    def tag(self, x: int, y: int, text: str, fill=ACCENT) -> None:
        font = _font(18, bold=True)
        w = int(self.draw.textlength(text, font=font)) + 24
        _rounded(self.draw, (x, y, x + w, y + 34), 17, fill)
        self.draw.text((x + 12, y + 6), text, font=font, fill=(255, 255, 255))

    def card(self, xy, radius: int = 12, fill=PANEL) -> None:
        _rounded(self.draw, xy, radius, fill)

    def metric(self, x: int, y: int, label: str, value: str, color=TEXT) -> None:
        self.draw.text((x, y), label, font=_font(17), fill=MUTED)
        self.draw.text((x, y + 24), value, font=_font(27, bold=True), fill=color)

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.image.save(path, "PNG", optimize=True)
        return path


def _output_path(cache_dir: Path, prefix: str) -> Path:
    return cache_dir / f"{prefix}_{int(time.time() * 1000)}.png"


def _draw_header(canvas: Canvas, user: dict[str, Any], mode: str, avatar: Image.Image | None) -> None:
    cover = user.get("cover") or {}
    username = _safe(user.get("username"), "Unknown")
    canvas.card((28, 28, canvas.width - 28, 210), 18, PANEL)
    if avatar:
        _paste_circle(canvas.image, avatar, (58, 58, 178, 178))
    else:
        _paste_circle(canvas.image, _placeholder((120, 120)), (58, 58, 178, 178))
    canvas.draw.text((204, 58), _fit_text(canvas.draw, username, _font(34, bold=True), 560), font=_font(34, bold=True), fill=TEXT)
    country = user.get("country") or {}
    subtitle = f"#{user.get('id')}  ·  {mode_label(mode)}  ·  {country.get('code') or '--'}"
    canvas.draw.text((206, 104), subtitle, font=_font(19), fill=MUTED)
    canvas.draw.text((206, 138), f"加入：{_date(user.get('join_date'))}", font=_font(18), fill=MUTED)
    if cover.get("custom_url") or cover.get("url"):
        canvas.tag(canvas.width - 180, 58, "profile", BLUE)


async def render_user_card(
    user: dict[str, Any],
    mode: str,
    cache_dir: Path,
    *,
    title: str = "osu! 用户信息",
    proxy: str = "",
    recent_scores: list[dict[str, Any]] | None = None,
) -> Path:
    visible_scores = (recent_scores or [])[:3]
    score_rows = max(0, len(visible_scores))
    canvas = Canvas(900, 620 + score_rows * 88)
    avatar = await fetch_avatar(user, cache_dir / "images", proxy=proxy)
    _draw_header(canvas, user, mode, avatar)
    stats = user.get("statistics") or {}
    level = stats.get("level") or {}
    grades = stats.get("grade_counts") or {}

    canvas.title(38, 236, title, "公开资料由 osu!api v2 查询")
    metrics = [
        ("全球排名", _rank_text(stats.get("global_rank")), ACCENT),
        ("国家排名", _rank_text(stats.get("country_rank")), BLUE),
        ("PP", _num(stats.get("pp"), 2), TEXT),
        ("准确率", _pct(stats.get("hit_accuracy")), GREEN),
        ("游玩次数", _num(stats.get("play_count")), TEXT),
        ("游玩时间", _duration(stats.get("play_time")), TEXT),
        ("最高连击", _num(stats.get("maximum_combo")), YELLOW),
        ("等级", f"{_num(level.get('current'))}.{_num(level.get('progress'))}%", TEXT),
    ]
    x0, y0 = 38, 314
    for i, (label, value, color) in enumerate(metrics):
        x = x0 + (i % 4) * 210
        y = y0 + (i // 4) * 100
        canvas.card((x, y, x + 190, y + 78), 10, PANEL)
        canvas.metric(x + 18, y + 14, label, value, color)

    canvas.card((38, 520, 862, 580), 12, PANEL)
    rank_text = (
        f"SS {grades.get('ssh', 0) + grades.get('ss', 0)}   "
        f"S {grades.get('sh', 0) + grades.get('s', 0)}   "
        f"A {grades.get('a', 0)}"
    )
    canvas.draw.text((60, 538), f"成绩等级：{rank_text}", font=_font(21, bold=True), fill=TEXT)
    canvas.draw.text((620, 540), "osu.ppy.sh/users/" + str(user.get("id")), font=_font(17), fill=MUTED)
    y = 608
    for idx, score in enumerate(visible_scores, start=1):
        score_title, version = _score_title(score)
        pp = score.get("pp")
        mods_text = _score_mods(score) or "NM"
        canvas.card((38, y, 862, y + 76), 12, PANEL)
        canvas.draw.text((60, y + 16), f"最近 #{idx}", font=_font(18, bold=True), fill=ACCENT)
        canvas.draw.text(
            (156, y + 12),
            _fit_text(canvas.draw, score_title, _font(20, bold=True), 420),
            font=_font(20, bold=True),
            fill=TEXT,
        )
        canvas.draw.text(
            (156, y + 40),
            _fit_text(canvas.draw, f"[{version}]  {mods_text}", _font(15), 420),
            font=_font(15),
            fill=MUTED,
        )
        canvas.draw.text((620, y + 14), _safe(score.get("rank")), font=_font(23, bold=True), fill=YELLOW)
        canvas.draw.text(
            (680, y + 15),
            f"{_num(pp, 2)}pp" if pp is not None else "no pp",
            font=_font(19, bold=True),
            fill=ACCENT if pp is not None else MUTED,
        )
        canvas.draw.text(
            (680, y + 42),
            f"{_pct(score.get('accuracy'))} · {_date(score.get('created_at'))}",
            font=_font(14),
            fill=MUTED,
        )
        y += 88
    return canvas.save(_output_path(cache_dir, "user"))


async def render_dashboard(
    user: dict[str, Any],
    scores: list[dict[str, Any]],
    mode: str,
    cache_dir: Path,
    *,
    proxy: str = "",
) -> Path:
    visible_scores = scores[:5]
    score_rows = max(1, len(visible_scores))
    score_start_y = 604
    height = score_start_y + score_rows * 98 + 42
    canvas = Canvas(940, height)
    avatar = await fetch_avatar(user, cache_dir / "images", proxy=proxy)
    _draw_header(canvas, user, mode, avatar)
    stats = user.get("statistics") or {}
    level = stats.get("level") or {}
    grades = stats.get("grade_counts") or {}
    canvas.title(
        38,
        236,
        "osu! 个人看板",
        f"{_rank_text(stats.get('global_rank'))} · {_num(stats.get('pp'), 2)}pp · {_pct(stats.get('hit_accuracy'))}",
    )

    metrics = [
        ("全球排名", _rank_text(stats.get("global_rank")), ACCENT),
        ("国家排名", _rank_text(stats.get("country_rank")), BLUE),
        ("PP", _num(stats.get("pp"), 2), TEXT),
        ("准确率", _pct(stats.get("hit_accuracy")), GREEN),
        ("游玩次数", _num(stats.get("play_count")), TEXT),
        ("游玩时间", _duration(stats.get("play_time")), TEXT),
        ("最高连击", _num(stats.get("maximum_combo")), YELLOW),
        ("等级", f"{_num(level.get('current'))}.{_num(level.get('progress'))}%", TEXT),
    ]
    for i, (label, value, color) in enumerate(metrics):
        x = 38 + (i % 4) * 224
        y = 318 + (i // 4) * 92
        canvas.card((x, y, x + 204, y + 72), 10, PANEL)
        canvas.metric(x + 16, y + 11, label, value, color)

    rank_text = (
        f"SS {grades.get('ssh', 0) + grades.get('ss', 0)}   "
        f"S {grades.get('sh', 0) + grades.get('s', 0)}   "
        f"A {grades.get('a', 0)}"
    )
    canvas.card((38, 508, 902, 558), 12, PANEL)
    canvas.draw.text((60, 523), f"成绩等级：{rank_text}", font=_font(19, bold=True), fill=TEXT)
    canvas.draw.text((690, 524), "最近成绩", font=_font(19, bold=True), fill=MUTED)

    y = score_start_y
    if not visible_scores:
        canvas.card((38, y, 902, y + 86), 12, PANEL)
        canvas.draw.text((62, y + 29), "暂无最近成绩。", font=_font(24, bold=True), fill=MUTED)
    for idx, score in enumerate(visible_scores, start=1):
        canvas.card((38, y, 902, y + 86), 12, PANEL)
        rank = _safe(score.get("rank"))
        pp = score.get("pp")
        title, version = _score_title(score)
        canvas.draw.text((62, y + 18), f"#{idx}", font=_font(24, bold=True), fill=ACCENT)
        canvas.draw.text((112, y + 14), _fit_text(canvas.draw, title, _font(22, bold=True), 500), font=_font(22, bold=True), fill=TEXT)
        canvas.draw.text((112, y + 44), _fit_text(canvas.draw, f"[{version}]", _font(17), 500), font=_font(17), fill=MUTED)
        canvas.draw.text((650, y + 14), f"{rank}", font=_font(26, bold=True), fill=YELLOW)
        canvas.draw.text((710, y + 18), f"{_num(pp, 2)}pp", font=_font(22, bold=True), fill=ACCENT if pp else MUTED)
        canvas.draw.text((710, y + 48), f"{_pct(score.get('accuracy'))} · {_num(score.get('score'))}", font=_font(16), fill=MUTED)
        y += 98
    return canvas.save(_output_path(cache_dir, "dashboard"))


async def render_scores(
    user: dict[str, Any],
    scores: list[dict[str, Any]],
    mode: str,
    score_type: str,
    cache_dir: Path,
) -> Path:
    height = 260 + max(1, len(scores)) * 104
    canvas = Canvas(980, height)
    canvas.title(38, 34, f"osu! {'最好成绩' if score_type == 'best' else '最近成绩'}", f"{user.get('username')} · {mode_label(mode)}")
    y = 126
    if not scores:
        canvas.card((38, y, 942, y + 92), 12, PANEL)
        canvas.draw.text((62, y + 32), "暂无成绩。", font=_font(24, bold=True), fill=MUTED)
    for idx, score in enumerate(scores, start=1):
        canvas.card((38, y, 942, y + 92), 12, PANEL)
        title, version = _score_title(score)
        mods_text = _score_mods(score)
        canvas.draw.text((62, y + 18), f"#{idx}", font=_font(24, bold=True), fill=ACCENT)
        canvas.draw.text((116, y + 14), _fit_text(canvas.draw, title, _font(21, bold=True), 520), font=_font(21, bold=True), fill=TEXT)
        canvas.draw.text((116, y + 44), _fit_text(canvas.draw, f"[{version}]  {mods_text or 'NM'}", _font(16), 520), font=_font(16), fill=MUTED)
        canvas.draw.text((674, y + 14), _safe(score.get("rank")), font=_font(26, bold=True), fill=YELLOW)
        canvas.draw.text((736, y + 16), f"{_num(score.get('pp'), 2)}pp", font=_font(21, bold=True), fill=ACCENT if score.get("pp") else MUTED)
        canvas.draw.text((736, y + 46), f"{_pct(score.get('accuracy'))} · {_num(score.get('max_combo'))}x · {_date(score.get('created_at'))}", font=_font(16), fill=MUTED)
        y += 104
    return canvas.save(_output_path(cache_dir, "scores"))


async def render_ranking(ranking: dict[str, Any], mode: str, cache_dir: Path, *, country: str | None = None, limit: int = 10) -> Path:
    entries = list(ranking.get("ranking") or [])[:limit]
    canvas = Canvas(920, 190 + max(1, len(entries)) * 76)
    title = f"osu! 排行榜 · {mode_label(mode)}"
    subtitle = f"范围：{country.upper()}" if country else "范围：全球"
    canvas.title(38, 34, title, subtitle)
    y = 126
    if not entries:
        canvas.card((38, y, 882, y + 72), 12, PANEL)
        canvas.draw.text((62, y + 22), "没有查询到排行榜数据。", font=_font(22, bold=True), fill=MUTED)
    for idx, entry in enumerate(entries, start=1):
        user = entry.get("user") if isinstance(entry.get("user"), dict) else entry
        stats = entry if "pp" in entry or "global_rank" in entry else user.get("statistics") or {}
        rank = idx if country else stats.get("global_rank") or idx
        canvas.card((38, y, 882, y + 64), 10, PANEL)
        canvas.draw.text((62, y + 16), f"#{_num(rank)}", font=_font(22, bold=True), fill=ACCENT)
        country_code = (user.get("country") or {}).get("code") or user.get("country_code") or "--"
        username = f"{_safe(user.get('username'), 'Unknown')} · {country_code}"
        canvas.draw.text((172, y + 16), _fit_text(canvas.draw, username, _font(22, bold=True), 360), font=_font(22, bold=True), fill=TEXT)
        canvas.draw.text((560, y + 16), f"{_num(stats.get('pp'), 2)}pp", font=_font(21, bold=True), fill=BLUE)
        canvas.draw.text((708, y + 18), _pct(stats.get("hit_accuracy")), font=_font(18), fill=MUTED)
        y += 76
    return canvas.save(_output_path(cache_dir, "ranking"))


async def render_beatmap(beatmap: dict[str, Any], cache_dir: Path, *, proxy: str = "") -> Path:
    beatmapset = beatmap.get("beatmapset") or {}
    cover_url = (beatmapset.get("covers") or {}).get("cover@2x") or (beatmapset.get("covers") or {}).get("cover")
    cover = await fetch_image(cover_url, cache_dir / "images", suffix=".jpg", proxy=proxy)
    canvas = Canvas(940, 610)
    if cover:
        cover = cover.resize((940, 240), Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(1.2))
        overlay = Image.new("RGBA", (940, 240), (0, 0, 0, 112))
        canvas.image.paste(Image.alpha_composite(cover.convert("RGBA"), overlay).convert("RGB"), (0, 0))
    else:
        canvas.image.paste(_placeholder((940, 240)), (0, 0))

    title = f"{beatmapset.get('artist') or '?'} - {beatmapset.get('title') or '?'}"
    canvas.draw.text((38, 42), _fit_text(canvas.draw, title, _font(32, bold=True), 850), font=_font(32, bold=True), fill=TEXT)
    canvas.draw.text((40, 90), f"[{beatmap.get('version') or '?'}]", font=_font(22), fill=TEXT)
    canvas.tag(40, 138, _safe(beatmapset.get("status"), "unknown"), ACCENT)

    metrics = [
        ("星级", f"{_num(beatmap.get('difficulty_rating'), 2)}★", ACCENT),
        ("BPM", _num(beatmap.get("bpm"), 1), TEXT),
        ("长度", f"{math.floor((beatmap.get('total_length') or 0) / 60)}:{int((beatmap.get('total_length') or 0) % 60):02d}", TEXT),
        ("最大连击", _num(beatmap.get("max_combo")), TEXT),
        ("CS/AR/OD/HP", f"{_num(beatmap.get('cs'), 1)}/{_num(beatmap.get('ar'), 1)}/{_num(beatmap.get('accuracy'), 1)}/{_num(beatmap.get('drain'), 1)}", BLUE),
        ("游玩/通过", f"{_num(beatmap.get('playcount'))}/{_num(beatmap.get('passcount'))}", GREEN),
    ]
    x0, y0 = 38, 286
    for i, (label, value, color) in enumerate(metrics):
        x = x0 + (i % 3) * 288
        y = y0 + (i // 3) * 100
        canvas.card((x, y, x + 260, y + 78), 10, PANEL)
        canvas.metric(x + 18, y + 14, label, value, color)

    url = beatmap.get("url") or f"https://osu.ppy.sh/beatmaps/{beatmap.get('id')}"
    canvas.card((38, 512, 902, 572), 12, PANEL)
    canvas.draw.text((60, 530), _fit_text(canvas.draw, url, _font(19), 800), font=_font(19), fill=MUTED)
    return canvas.save(_output_path(cache_dir, "beatmap"))


async def render_beatmap_search(result: dict[str, Any], query: str, mode: str, cache_dir: Path, *, limit: int = 5) -> Path:
    sets = list(result.get("beatmapsets") or [])[:limit]
    canvas = Canvas(980, 190 + max(1, len(sets)) * 100)
    canvas.title(38, 34, "osu! 谱面搜索", f"{mode_label(mode)} · {query}")
    y = 126
    if not sets:
        canvas.card((38, y, 942, y + 92), 12, PANEL)
        canvas.draw.text((62, y + 32), "没有找到谱面。", font=_font(24, bold=True), fill=MUTED)
    for idx, beatmapset in enumerate(sets, start=1):
        beatmaps = beatmapset.get("beatmaps") or []
        stars = [b.get("difficulty_rating") for b in beatmaps if b.get("difficulty_rating") is not None]
        star_text = "-"
        if stars:
            star_text = f"{min(stars):.2f}★ - {max(stars):.2f}★"
        title = f"{beatmapset.get('artist') or '?'} - {beatmapset.get('title') or '?'}"
        canvas.card((38, y, 942, y + 86), 12, PANEL)
        canvas.draw.text((62, y + 18), f"#{idx}", font=_font(24, bold=True), fill=ACCENT)
        canvas.draw.text((118, y + 14), _fit_text(canvas.draw, title, _font(22, bold=True), 560), font=_font(22, bold=True), fill=TEXT)
        canvas.draw.text((118, y + 44), f"ID {beatmapset.get('id')} · {beatmapset.get('status')} · {len(beatmaps)} 难度", font=_font(16), fill=MUTED)
        canvas.draw.text((730, y + 18), star_text, font=_font(22, bold=True), fill=YELLOW)
        y += 100
    return canvas.save(_output_path(cache_dir, "beatmap_search"))


async def render_notice(title: str, lines: list[str], cache_dir: Path) -> Path:
    height = 180 + max(1, len(lines)) * 34
    canvas = Canvas(820, height)
    canvas.title(38, 34, title, "osu! 信息查询")
    y = 126
    canvas.card((38, y - 12, 782, height - 34), 12, PANEL)
    for line in lines:
        for wrapped in _wrap_text(canvas.draw, line, _font(20), 690, 3):
            canvas.draw.text((62, y), wrapped, font=_font(20), fill=TEXT)
            y += 34
    return canvas.save(_output_path(cache_dir, "notice"))
