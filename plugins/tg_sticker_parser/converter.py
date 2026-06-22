from __future__ import annotations

from pathlib import Path

from plugins.media_transcoder import (
    StickerGifOptions,
    TranscodeError as ConvertError,
    ensure_sticker_gif,
    run_cmd,
)


class StickerConverter:
    """兼容旧导入；实际转码逻辑由 plugins.media_transcoder 维护。"""

    def __init__(
        self,
        gif_fps: int,
        gif_width: int,
        gif_max_colors: int,
        tgs_converter_cmd: list[str],
        gif_dither: str = "sierra2_4a",
    ) -> None:
        self.options = StickerGifOptions(
            fps=int(gif_fps),
            width=int(gif_width),
            max_colors=int(gif_max_colors),
            dither=str(gif_dither),
            tgs_converter_cmd=list(tgs_converter_cmd),
        )

    async def to_gif(self, input_path: Path, output_path: Path) -> Path:
        return await ensure_sticker_gif(input_path, output_path, options=self.options)
