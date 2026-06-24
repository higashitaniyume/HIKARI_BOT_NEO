from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import ImageFont

from core.resources import load_json_resource

DEFAULT_RENDERING: dict[str, Any] = {
    "font_regular": "",
    "font_bold": "",
    "fallback_fonts_regular": [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "C:/Windows/Fonts/NotoSansSC-VF.ttf",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simsun.ttc",
    ],
    "fallback_fonts_bold": [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "C:/Windows/Fonts/NotoSansSC-VF.ttf",
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ],
}


def get_rendering_config() -> dict[str, Any]:
    cfg = DEFAULT_RENDERING.copy()
    cfg.update(load_json_resource("rendering.json", DEFAULT_RENDERING))
    return cfg


def _path_candidates(value: Any) -> list[Path]:
    values = value if isinstance(value, list) else [value]
    result: list[Path] = []
    for item in values:
        text = str(item or "").strip()
        if text:
            result.append(Path(text))
    return result


def font_candidates(*, bold: bool = False) -> list[Path]:
    cfg = get_rendering_config()
    primary = cfg.get("font_bold" if bold else "font_regular")
    fallback = cfg.get("fallback_fonts_bold" if bold else "fallback_fonts_regular")
    return [*_path_candidates(primary), *_path_candidates(fallback)]


def load_font(size: int, *, bold: bool = False):
    for path in font_candidates(bold=bold):
        if not path.exists():
            continue
        try:
            return ImageFont.truetype(str(path), size=size)
        except Exception:
            continue
    return ImageFont.load_default()
