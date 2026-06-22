from __future__ import annotations

import asyncio
from pathlib import Path


class ConvertError(RuntimeError):
    """贴纸转换异常。"""


async def run_cmd(cmd: list[str], timeout: int = 180) -> None:
    """异步执行外部命令，例如 ffmpeg / lottie_convert.py。"""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise ConvertError(f"命令超时: {' '.join(cmd)}")

    if proc.returncode != 0:
        raise ConvertError(
            "命令执行失败:\n"
            f"cmd={' '.join(cmd)}\n"
            f"stdout={stdout.decode(errors='ignore')}\n"
            f"stderr={stderr.decode(errors='ignore')}"
        )


class StickerConverter:
    """Telegram 贴纸转 GIF。"""

    def __init__(
        self,
        gif_fps: int,
        gif_width: int,
        gif_max_colors: int,
        tgs_converter_cmd: list[str],
    ) -> None:
        self.gif_fps = int(gif_fps)
        self.gif_width = int(gif_width)
        self.gif_max_colors = int(gif_max_colors)
        self.tgs_converter_cmd = list(tgs_converter_cmd)

    async def to_gif(self, input_path: Path, output_path: Path) -> Path:
        """根据输入格式自动转换为 GIF。"""
        suffix = input_path.suffix.lower()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if suffix == ".webp":
            await self._webp_to_gif(input_path, output_path)
        elif suffix == ".webm":
            await self._webm_to_gif(input_path, output_path)
        elif suffix == ".tgs":
            await self._tgs_to_gif(input_path, output_path)
        else:
            raise ConvertError(f"不支持的贴纸格式: {input_path}")

        if not output_path.exists() or output_path.stat().st_size <= 0:
            raise ConvertError(f"GIF 输出文件无效: {output_path}")

        return output_path

    async def _webp_to_gif(self, input_path: Path, output_path: Path) -> None:
        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_path),
            "-vf",
            f"scale={self.gif_width}:-1:flags=lanczos",
            "-loop",
            "0",
            str(output_path),
        ]
        await run_cmd(cmd, timeout=60)

    async def _webm_to_gif(self, input_path: Path, output_path: Path) -> None:
        vf = (
            f"[0:v]fps={self.gif_fps},"
            f"scale={self.gif_width}:-1:flags=lanczos,"
            f"split[s0][s1];"
            f"[s0]palettegen=max_colors={self.gif_max_colors}[p];"
            f"[s1][p]paletteuse=dither=bayer"
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

    async def _tgs_to_gif(self, input_path: Path, output_path: Path) -> None:
        cmd = [
            *self.tgs_converter_cmd,
            "--fps",
            str(self.gif_fps),
            "--width",
            str(self.gif_width),
            "--height",
            str(self.gif_width),
            str(input_path),
            str(output_path),
        ]
        await run_cmd(cmd, timeout=240)