from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any

from nonebot.adapters.onebot.v11 import Bot, MessageEvent

from core.message_pipeline import register_handler
from core.stats_tracker import increment as stats_increment

from .config import get_config
from .converter import StickerConverter
from .sender import send_sticker_outputs
from .tg_api import TelegramBotApi, extract_sticker_set_names, guess_extension

logger = logging.getLogger("HikariBot.TgStickerPlugin")

# 触发首次加载并确保配置存在
get_config()


class AutoTgStickerHandler:
    """自动检测 Telegram 贴纸包链接并转换为 GIF 发送。"""

    name = "TgStickerParser"

    async def match(self, event: MessageEvent, text: str) -> bool:
        cfg = get_config()
        if not cfg.get("enabled", True):
            return False
        if not cfg.get("auto_parse", True):
            return False
        return bool(extract_sticker_set_names(text))

    async def handle(self, bot: Bot, event: MessageEvent) -> None:
        cfg = get_config()
        text = str(event.get_message())
        set_names = extract_sticker_set_names(text)

        if not set_names:
            return

        # 防刷屏：一条消息最多处理 1 个 Telegram 贴纸包
        set_name = set_names[0]

        logger.info(
            "[TgSticker] 自动解析触发 → user=%s, set_name=%s",
            event.get_user_id(),
            set_name,
        )

        result = await parse_sticker_set_to_gifs(bot, event, set_name, cfg)

        gif_paths = result["gif_paths"]
        output_root = result["output_root"]
        title = result["title"]
        total_count = result.get("total_count", 0)
        failed_count = result.get("failed_count", 0)

        if not gif_paths:
            await bot.send(event, "没有成功转换出可发送的 GIF。")
            return

        await send_sticker_outputs(
            bot=bot,
            event=event,
            gif_paths=gif_paths,
            output_root=output_root,
            set_name=set_name,
            title=title,
            total_count=total_count,
            failed_count=failed_count,
            direct_send_limit=int(cfg.get("direct_send_limit", 10)),
            merged_send_limit=int(cfg.get("merged_send_limit", 80)),
            send_delay_seconds=float(cfg.get("send_delay_seconds", 0.5)),
        )

        stats_increment(event, "tg_sticker_parsed", 1)


async def parse_sticker_set_to_gifs(
    bot: Bot,
    event: MessageEvent,
    set_name: str,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    output_root = Path(str(cfg.get("output_root", "/tmp/hikari_bot/tg_stickers"))) / set_name
    originals_dir = output_root / "originals"
    gifs_dir = output_root / "gifs"
    output_root.mkdir(parents=True, exist_ok=True)

    api = TelegramBotApi(
        token=str(cfg.get("bot_token", "")),
        api_base=str(cfg.get("api_base", "https://api.telegram.org")),
        proxy=str(cfg.get("proxy", "")),
    )

    try:
        # await bot.send(event, f"检测到 Telegram 贴纸包：{set_name}\n开始获取贴纸列表……")

        sticker_set = await api.get_sticker_set(set_name)
        title = sticker_set.get("title") or set_name
        stickers = sticker_set.get("stickers") or []

        if not stickers:
            await bot.send(event, "这个贴纸包里没有可处理的贴纸。")
            return {
                "gif_paths": [],
                "output_root": output_root,
                "title": title,
            }

        # Removed intermediate starting message to reduce spam

        converter = StickerConverter(
            gif_fps=int(cfg.get("gif_fps", 12)),
            gif_width=int(cfg.get("gif_width", 512)),
            gif_max_colors=int(cfg.get("gif_max_colors", 128)),
            tgs_converter_cmd=list(
                cfg.get("tgs_converter_cmd", ["uv", "run", "lottie_convert.py"])
            ),
        )

        sem = asyncio.Semaphore(max(1, int(cfg.get("ffmpeg_concurrency", 2))))

        async def process_one(index: int, sticker: dict[str, Any]) -> Path | None:
            async with sem:
                file_unique_id = sticker.get("file_unique_id") or f"unknown_{index}"
                file_id = sticker.get("file_id")

                if not file_id:
                    logger.warning("[TgSticker] 第 %s 个贴纸缺少 file_id，跳过", index)
                    return None

                try:
                    file_info = await api.get_file(file_id)
                    file_path = file_info["file_path"]
                    ext = guess_extension(sticker, file_path)

                    original_path = originals_dir / f"{index:03d}_{file_unique_id}{ext}"
                    gif_path = gifs_dir / f"{index:03d}_{file_unique_id}.gif"

                    if not original_path.exists():
                        await api.download_file(file_path, original_path)

                    if not gif_path.exists():
                        await converter.to_gif(original_path, gif_path)

                    if gif_path.exists() and gif_path.stat().st_size > 0:
                        return gif_path

                    logger.warning("[TgSticker] 第 %s 个贴纸转换后文件无效: %s", index, gif_path)
                    return None

                except Exception as e:
                    logger.exception(
                        "[TgSticker] 第 %s 个贴纸处理失败，已跳过: %s",
                        index,
                        e,
                    )
                    return None

        tasks = [
            process_one(index, sticker)
            for index, sticker in enumerate(stickers, start=1)
        ]

        results = await asyncio.gather(*tasks)
        gif_paths = [p for p in results if isinstance(p, Path) and p.exists() and p.stat().st_size > 0]

        failed_count = len(stickers) - len(gif_paths)

        # Moved completion message to sender.py for consolidation

        if not bool(cfg.get("keep_original", True)):
            shutil.rmtree(originals_dir, ignore_errors=True)

        return {
            "gif_paths": gif_paths,
            "output_root": output_root,
            "title": title,
            "total_count": len(stickers),
            "failed_count": failed_count,
        }

    finally:
        await api.close()


register_handler(AutoTgStickerHandler())
logger.info("Telegram 贴纸包解析器已注册 → t.me/addstickers")