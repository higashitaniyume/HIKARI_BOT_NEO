from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import get_config

logger = logging.getLogger("HikariBot.MediaTranscoder")

STICKER_INPUT_EXTS = {
    ".gif",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".mp4",
    ".webm",
    ".mov",
    ".mkv",
    ".tgs",
}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv"}


class TranscodeError(RuntimeError):
    """媒体转码异常。"""


@dataclass(slots=True)
class StickerGifOptions:
    fps: int
    width: int
    max_colors: int
    dither: str
    tgs_converter_cmd: list[str]

    @classmethod
    def from_config(cls, overrides: dict[str, Any] | None = None) -> "StickerGifOptions":
        cfg = get_config()
        if overrides:
            cfg.update(overrides)

        return cls(
            fps=int(cfg.get("sticker_gif_fps", cfg.get("gif_fps", 12))),
            width=int(cfg.get("sticker_gif_width", cfg.get("gif_width", 0))),
            max_colors=int(cfg.get("sticker_gif_max_colors", cfg.get("gif_max_colors", 256))),
            dither=str(cfg.get("sticker_gif_dither", cfg.get("gif_dither", "sierra2_4a"))),
            tgs_converter_cmd=list(
                cfg.get("tgs_converter_cmd", ["uv", "run", "lottie_convert.py"])
            ),
        )


async def run_cmd(cmd: list[str], timeout: int = 180) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as e:
        proc.kill()
        raise TranscodeError(f"命令超时: {' '.join(cmd)}") from e

    if proc.returncode != 0:
        raise TranscodeError(
            "命令执行失败:\n"
            f"cmd={' '.join(cmd)}\n"
            f"stdout={stdout.decode(errors='ignore')}\n"
            f"stderr={stderr.decode(errors='ignore')}"
        )


async def ensure_sticker_gif(
    input_path: Path,
    output_path: Path,
    *,
    options: StickerGifOptions | None = None,
) -> Path:
    """把贴纸素材统一整理为 GIF，尽量保留原始尺寸、色彩和透明通道。"""
    input_path = Path(input_path)
    output_path = Path(output_path)
    suffix = input_path.suffix.lower()

    if suffix not in STICKER_INPUT_EXTS:
        raise TranscodeError(f"不支持的贴纸素材格式: {input_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if suffix == ".gif":
        if input_path.resolve() != output_path.resolve():
            shutil.copy2(input_path, output_path)
    elif suffix == ".tgs":
        await _tgs_to_gif(input_path, output_path, options or StickerGifOptions.from_config())
    else:
        await _ffmpeg_to_gif(input_path, output_path, options or StickerGifOptions.from_config())

    if not output_path.exists() or output_path.stat().st_size <= 0:
        raise TranscodeError(f"GIF 输出文件无效: {output_path}")

    return output_path


async def _ffmpeg_to_gif(input_path: Path, output_path: Path, options: StickerGifOptions) -> None:
    filters: list[str] = []

    if input_path.suffix.lower() in VIDEO_EXTS or input_path.suffix.lower() == ".webp":
        filters.append(f"fps={options.fps}")

    if options.width > 0:
        filters.append(f"scale={options.width}:-1:flags=lanczos")

    filters.append("split[s0][s1]")
    filter_prefix = "[0:v]" + ",".join(filters)
    vf = (
        f"{filter_prefix};"
        f"[s0]palettegen=reserve_transparent=on:max_colors={options.max_colors}[p];"
        f"[s1][p]paletteuse=dither={options.dither}:alpha_threshold=128"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-filter_complex",
        vf,
        "-loop",
        "0",
        str(output_path),
    ]
    await run_cmd(cmd, timeout=180)


async def _tgs_to_gif(input_path: Path, output_path: Path, options: StickerGifOptions) -> None:
    cmd = [
        *options.tgs_converter_cmd,
        "--fps",
        str(options.fps),
        "--width",
        str(options.width or 512),
        "--height",
        str(options.width or 512),
        str(input_path),
        str(output_path),
    ]
    await run_cmd(cmd, timeout=240)


logger.info("媒体转码服务已加载")
