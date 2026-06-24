from __future__ import annotations

import asyncio
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nonebot.adapters.onebot.v11 import Bot, MessageEvent

from core.bot_messages import get_message as msg
from core.message_pipeline import register_handler
from core.stats_tracker import increment as stats_increment
from plugins import sticker_library
from plugins.media_transcoder import StickerGifOptions, ensure_sticker_gif
from plugins.media_transcoder.config import get_config as get_transcoder_config

from .config import get_config
from .sender import send_sticker_outputs
from .tg_api import TelegramBotApi, extract_sticker_set_names, guess_extension

logger = logging.getLogger("HikariBot.TgStickerPlugin")

# 触发首次加载并确保配置存在
get_config()

MEDIA_EXTS = {".gif"}


@dataclass(slots=True)
class TgStickerOptions:
    use_zip: bool = False
    refresh: bool = False
    save_pack: bool = True
    trigger_keyword: str = ""


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
        options = parse_options(text)
        trigger_keyword = options.trigger_keyword or set_name

        logger.info(
            "[TgSticker] 自动解析触发 → user=%s, set_name=%s, options=%s",
            event.get_user_id(),
            set_name,
            options,
        )

        cached_gifs = find_saved_gifs(set_name)
        if cached_gifs and not options.refresh:
            logger.info("[TgSticker] 使用已保存贴纸包缓存 → %s (%d 个)", set_name, len(cached_gifs))
            cached_output_root = Path(str(cfg.get("output_root", "/tmp/hikari_bot/tg_stickers"))) / set_name / "cached_send"
            sendable_gifs = prepare_cached_gifs_for_send(cached_gifs, cached_output_root)
            if options.save_pack:
                await register_sticker_trigger(set_name, trigger_keyword)
            await send_sticker_outputs(
                bot=bot,
                event=event,
                gif_paths=sendable_gifs,
                output_root=cached_output_root,
                set_name=set_name,
                title=set_name,
                total_count=len(sendable_gifs),
                failed_count=0,
                direct_send_limit=get_direct_send_limit(cfg),
                merged_send_limit=int(cfg.get("merged_send_limit", 80)),
                send_delay_seconds=float(cfg.get("send_delay_seconds", 0.5)),
                use_zip=options.use_zip,
                from_cache=True,
            )
            stats_increment(event, "tg_sticker_parsed", 1)
            return

        result = await parse_sticker_set_to_gifs(bot, event, set_name, cfg)

        gif_paths = result["gif_paths"]
        output_root = result["output_root"]
        title = result["title"]
        total_count = result.get("total_count", 0)
        failed_count = result.get("failed_count", 0)

        if not gif_paths:
            await bot.send(event, msg("tg_sticker.no_gif"))
            return

        if options.save_pack:
            try:
                saved_paths = save_gifs_to_pack(set_name, gif_paths)
                await register_sticker_trigger(set_name, trigger_keyword)
                logger.info("[TgSticker] 已保存 %d 个 GIF 并注册触发词 %s", len(saved_paths), trigger_keyword)
            except Exception as e:
                logger.exception("[TgSticker] 自动保存表情包或更新配置失败: %s", e)
        else:
            logger.info("[TgSticker] nosave 已启用，不保存贴纸包 → %s", set_name)

        await send_sticker_outputs(
            bot=bot,
            event=event,
            gif_paths=gif_paths,
            output_root=output_root,
            set_name=set_name,
            title=title,
            total_count=total_count,
            failed_count=failed_count,
            direct_send_limit=get_direct_send_limit(cfg),
            merged_send_limit=int(cfg.get("merged_send_limit", 80)),
            send_delay_seconds=float(cfg.get("send_delay_seconds", 0.5)),
            use_zip=options.use_zip,
            from_cache=False,
        )

        stats_increment(event, "tg_sticker_parsed", 1)


def parse_options(text: str) -> TgStickerOptions:
    """解析链接后的简单参数：zip / refresh / nosave / name=xxx / keyword=xxx。"""
    tokens = [token.strip() for token in re.split(r"\s+", text.strip()) if token.strip()]
    options = TgStickerOptions()

    i = 0
    while i < len(tokens):
        token = tokens[i]
        lower = token.lower()

        if lower == "zip":
            options.use_zip = True
        elif lower in {"refresh", "reload", "force"}:
            options.refresh = True
        elif lower in {"nosave", "no-save"}:
            options.save_pack = False
        elif lower.startswith(("name=", "keyword=", "kw=")):
            _, value = token.split("=", 1)
            value = value.strip()
            if value:
                options.trigger_keyword = value
        elif token.startswith("关键词"):
            value = token.removeprefix("关键词").strip("=:： ")
            if not value and i + 1 < len(tokens):
                i += 1
                value = tokens[i].strip()
            if value:
                options.trigger_keyword = value

        i += 1

    return options


def get_direct_send_limit(cfg: dict[str, Any]) -> int:
    """兼容旧配置 max_send_count 和新配置 direct_send_limit。"""
    return int(cfg.get("direct_send_limit", cfg.get("max_send_count", 10)))


def find_saved_gifs(set_name: str) -> list[Path]:
    """读取已保存到本地贴纸库的 GIF。"""
    return sticker_library.get_pack_files(set_name)


def prepare_cached_gifs_for_send(gif_paths: list[Path], output_root: Path) -> list[Path]:
    """复制本地贴纸包缓存到 NapCat 可读的共享目录后再发送。"""
    output_root.mkdir(parents=True, exist_ok=True)

    sendable_paths: list[Path] = []
    for gif_path in gif_paths:
        if not gif_path.exists() or gif_path.stat().st_size <= 0:
            continue
        dest = output_root / gif_path.name
        if not dest.exists() or dest.stat().st_size != gif_path.stat().st_size:
            shutil.copy2(gif_path, dest)
        sendable_paths.append(dest)

    return sendable_paths


def save_gifs_to_pack(set_name: str, gif_paths: list[Path]) -> list[Path]:
    """保存转换结果到本地贴纸库。"""
    return sticker_library.save_gifs_to_pack(set_name, gif_paths, source="telegram")


async def register_sticker_trigger(set_name: str, keyword: str) -> None:
    """把贴纸包注册到本地贴纸库。"""
    sticker_library.register_pack_keywords(set_name, keyword, include_pack_name=True)


async def parse_sticker_set_to_gifs(
    bot: Bot | None,
    event: MessageEvent | None,
    set_name: str,
    cfg: dict[str, Any],
    progress_callback: Any = None,
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
        sticker_set = await api.get_sticker_set(set_name)
        title = sticker_set.get("title") or set_name
        stickers = sticker_set.get("stickers") or []

        if not stickers:
            if bot is not None and event is not None:
                await bot.send(event, msg("tg_sticker.empty_pack"))
            return {
                "gif_paths": [],
                "output_root": output_root,
                "title": title,
                "total_count": 0,
                "failed_count": 0,
                "failed_items": [],
            }

        if bot is not None and event is not None:
            await bot.send(event, msg("tg_sticker.detected", title=title, count=len(stickers)))
        if progress_callback is not None:
            maybe_awaitable = progress_callback({
                "title": title,
                "total": len(stickers),
                "processed": 0,
                "message": f"检测到 Telegram 贴纸包：{title}",
            })
            if asyncio.iscoroutine(maybe_awaitable):
                await maybe_awaitable

        transcoder_cfg = get_transcoder_config()
        transcode_options = StickerGifOptions.from_config(transcoder_cfg)
        sem = asyncio.Semaphore(max(1, int(transcoder_cfg.get("sticker_ffmpeg_concurrency", 2))))

        async def process_one(index: int, sticker: dict[str, Any]) -> tuple[Path | None, str | None]:
            async with sem:
                file_unique_id = sticker.get("file_unique_id") or f"unknown_{index}"
                file_id = sticker.get("file_id")

                if not file_id:
                    logger.warning("[TgSticker] 第 %s 个贴纸缺少 file_id，跳过", index)
                    return None, f"第 {index} 个贴纸缺少 file_id"

                try:
                    file_info = await api.get_file(file_id)
                    file_path = file_info["file_path"]
                    ext = guess_extension(sticker, file_path)

                    original_path = originals_dir / f"{index:03d}_{file_unique_id}{ext}"
                    gif_path = gifs_dir / f"{index:03d}_{file_unique_id}.gif"

                    if not original_path.exists() or original_path.stat().st_size <= 0:
                        await api.download_file(file_path, original_path)

                    if not gif_path.exists() or gif_path.stat().st_size <= 0:
                        await ensure_sticker_gif(
                            original_path,
                            gif_path,
                            options=transcode_options,
                        )

                    if gif_path.exists() and gif_path.stat().st_size > 0:
                        return gif_path, None

                    logger.warning("[TgSticker] 第 %s 个贴纸转换后文件无效: %s", index, gif_path)
                    return None, f"第 {index} 个贴纸转换后文件无效"

                except Exception as e:
                    logger.exception(
                        "[TgSticker] 第 %s 个贴纸处理失败，已跳过: %s",
                        index,
                        e,
                    )
                    return None, f"第 {index} 个贴纸处理失败：{e}"

        tasks = [
            process_one(index, sticker)
            for index, sticker in enumerate(stickers, start=1)
        ]

        results: list[tuple[Path | None, str | None]] = []
        processed_count = 0
        for task in asyncio.as_completed(tasks):
            results.append(await task)
            processed_count += 1
            if progress_callback is not None:
                maybe_awaitable = progress_callback({
                    "title": title,
                    "total": len(stickers),
                    "processed": processed_count,
                    "message": f"正在转换 {processed_count}/{len(stickers)}：{title}",
                })
                if asyncio.iscoroutine(maybe_awaitable):
                    await maybe_awaitable
        gif_paths = [p for p, _ in results if isinstance(p, Path) and p.exists() and p.stat().st_size > 0]
        failed_items = [failure for _, failure in results if failure]

        failed_count = len(stickers) - len(gif_paths)

        if not bool(cfg.get("keep_original", True)):
            shutil.rmtree(originals_dir, ignore_errors=True)

        return {
            "gif_paths": gif_paths,
            "output_root": output_root,
            "title": title,
            "total_count": len(stickers),
            "failed_count": failed_count,
            "failed_items": failed_items,
        }

    finally:
        await api.close()


register_handler(AutoTgStickerHandler())
logger.info("Telegram 贴纸包解析器已注册 → t.me/addstickers")
