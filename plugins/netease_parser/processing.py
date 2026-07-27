"""
网易云音乐解析插件处理逻辑。

包含队列条目处理和单曲/播客/专辑的完整解析流程。
"""

import asyncio
import logging
import time
from typing import Any

from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent
from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent

from core.activity_tracker import ActivityScope
from core.bot_messages import get_message as msg
from core.error_notifier import notify_error_to_superuser, send_user_error
from core.stats_tracker import increment as stats_increment

from .config import get_config
from .downloader import download_audio
from .api import (
    fetch_album_detail,
    fetch_program_detail,
    fetch_song_detail,
    fetch_song_url,
)
from .sender import send_song
from . import NeteaseQueueItem, _sanitize_filename

logger = logging.getLogger("HikariBot.NeteasePlugin")


async def _process_queue_item(item: NeteaseQueueItem, cfg: dict) -> None:
    """执行单个队列条目（歌曲或播客），带重试。"""
    label = f"{item.item_type}_{item.item_id}"
    retry_count = max(0, int(cfg.get("parse_retry_count", 2)))
    retry_delay = max(0.0, float(cfg.get("parse_retry_delay_seconds", 2.0)))
    max_attempts = retry_count + 1

    for attempt in range(1, max_attempts + 1):
        try:
            with ActivityScope(
                "netease_parser",
                "parsing",
                f"解析网易云{item.item_type}",
                description=f"{item.item_type}={item.item_id}",
            ):
                if item.item_type == "program":
                    await _process_single_program(item.bot, item.event, item.item_id, cfg)
                elif item.item_type == "album":
                    await _process_single_album(item.bot, item.event, item.item_id, cfg)
                else:
                    await _process_single_song(item.bot, item.event, item.item_id, cfg)
            stats_increment(item.event, "netease_parsed", 1)
            return  # 成功，不重试
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if attempt < max_attempts:
                logger.warning(
                    "[Netease] 重试 %d/%d → %s error=%s",
                    attempt, retry_count, label, e,
                )
                await asyncio.sleep(retry_delay)
            else:
                logger.exception("[Netease] ✗ %s 重试耗尽 (共 %d 次) → %s", label, max_attempts, e)
                try:
                    await send_user_error(item.bot, item.event)
                    await notify_error_to_superuser(item.bot, item.event, e, "NeteaseParser")
                except Exception as notify_err:
                    logger.exception("发送错误通知失败: %s", notify_err)


async def _process_single_program(
    bot: Bot,
    event: MessageEvent,
    program_id: str,
    cfg: dict,
) -> None:
    """处理播客/电台节目：获取节目详情 → 提取 mainSong ID → 获取音频 URL → 下载 → 发送。"""
    session_start = time.time()
    api_base = str(cfg.get("api_base_url", "http://127.0.0.1:3000"))
    api_timeout = int(cfg.get("api_timeout", 30))
    real_ip = str(cfg.get("real_ip", "")).strip()
    high_quality = bool(cfg.get("high_quality", True))
    cookie = str(cfg.get("cookie", "")).strip()
    cache_dir = str(cfg.get("cache_dir", "/tmp/hikari_bot/netease"))
    max_file_mb = int(cfg.get("max_file_mb", 50))
    cache_ttl = int(cfg.get("cache_ttl_seconds", 600))

    log_extra = f"program_id={program_id} api={api_base} hq={high_quality} cookie={'已配置' if cookie else '未配置'}"
    logger.info("[Netease] ⏳ 开始处理播客节目 → %s", log_extra)

    # ===== 步骤 1: 获取节目详情 =====
    step_start = time.time()
    logger.info("[Netease] ▶ 步骤 1/4: 获取播客节目详情 → id=%s", program_id)
    try:
        program = await fetch_program_detail(program_id, api_base, api_timeout, real_ip, cookie)
    except Exception as e:
        elapsed = time.time() - step_start
        logger.error("[Netease] ✗ 步骤 1/4 失败 (%.1fs) → %s | %s", elapsed, e, log_extra)
        raise

    step_elapsed = time.time() - step_start
    if not program or not program.name:
        logger.warning("[Netease] ✗ 步骤 1/4 完成 (%.1fs) → 未找到节目信息, id=%s", step_elapsed, program_id)
        await bot.send(event, Message(msg("netease.not_found")))
        return

    song_id = program.id
    if not song_id:
        logger.warning("[Netease] ✗ 步骤 1/4 完成 (%.1fs) → 节目无 mainSong ID, id=%s", step_elapsed, program_id)
        await bot.send(event, Message(msg("netease.not_found")))
        return

    logger.info(
        "[Netease] ✓ 步骤 1/4 完成 (%.1fs) → %s — %s (mainSong.id=%s)",
        step_elapsed, program.name, program.artist, song_id,
    )

    # ===== 步骤 2: 获取音频 URL（用 mainSong.id） =====
    step_start = time.time()
    hq_label = "高音质" if high_quality else "标准"
    logger.info("[Netease] ▶ 步骤 2/4: 获取音频 URL → mainSong.id=%s (%s)", song_id, hq_label)
    try:
        url_result = await fetch_song_url(song_id, api_base, api_timeout, real_ip, high_quality, cookie)
    except Exception as e:
        elapsed = time.time() - step_start
        logger.error("[Netease] ✗ 步骤 2/4 失败 (%.1fs) → %s | %s", elapsed, e, log_extra)
        raise

    step_elapsed = time.time() - step_start
    if not url_result.url:
        logger.warning("[Netease] ✗ 步骤 2/4 完成 (%.1fs) → 音频链接不可用 | id=%s", step_elapsed, song_id)
        await bot.send(event, Message(msg("netease.url_unavailable")))
        return

    file_ext = f".{url_result.type}" if url_result.type in ("flac", "ogg", "wav") else ".mp3"
    logger.info(
        "[Netease] ✓ 步骤 2/4 完成 (%.1fs) → br=%skbps, type=%s, size=%.1fMB",
        step_elapsed, url_result.br // 1000, file_ext, url_result.size / 1024 / 1024,
    )

    # ===== 步骤 3: 下载音频 =====
    step_start = time.time()
    logger.info("[Netease] ▶ 步骤 3/4: 下载音频 → type=%s, max_size=%dMB", file_ext, max_file_mb)
    try:
        audio_path = await download_audio(
            url_result.url,
            cache_dir=cache_dir,
            timeout=api_timeout,
            max_file_mb=max_file_mb,
            cache_ttl_seconds=cache_ttl,
            file_ext=file_ext,
        )
    except Exception as e:
        elapsed = time.time() - step_start
        logger.error("[Netease] ✗ 步骤 3/4 失败 (%.1fs) → %s | %s", elapsed, e, log_extra)
        raise

    step_elapsed = time.time() - step_start
    file_size = audio_path.stat().st_size
    logger.info("[Netease] ✓ 步骤 3/4 完成 (%.1fs) → %s (%.1fMB)", step_elapsed, audio_path.name, file_size / 1024 / 1024)

    # ===== 步骤 4: 发送 =====
    step_start = time.time()
    logger.info("[Netease] ▶ 步骤 4/4: 发送音频 → id=%s", song_id)
    try:
        await send_song(bot, event, program, audio_path, cfg)
    except Exception as e:
        elapsed = time.time() - step_start
        logger.error("[Netease] ✗ 步骤 4/4 失败 (%.1fs) → %s | %s", elapsed, e, log_extra)
        raise

    step_elapsed = time.time() - step_start
    total_elapsed = time.time() - session_start
    logger.info(
        "[Netease] ✓ 步骤 4/4 完成 (%.1fs) | 🎉 播客处理完成 (总耗时 %.1fs) → %s — %s",
        step_elapsed, total_elapsed, program.name, program.artist,
    )


async def _process_single_album(
    bot: Bot,
    event: MessageEvent,
    album_id: str,
    cfg: dict,
) -> None:
    """
    处理专辑：顺序逐首处理，每首歌 → 发信息 → 上传文件 → 下一首。

    保证顺序：信息-文件-信息-文件-... 不会乱序。
    """
    session_start = time.time()
    api_base = str(cfg.get("api_base_url", "http://127.0.0.1:3000"))
    api_timeout = int(cfg.get("api_timeout", 30))
    real_ip = str(cfg.get("real_ip", "")).strip()
    high_quality = bool(cfg.get("high_quality", True))
    cookie = str(cfg.get("cookie", "")).strip()
    cache_dir = str(cfg.get("cache_dir", "/tmp/hikari_bot/netease"))
    max_file_mb = int(cfg.get("max_file_mb", 50))
    cache_ttl = int(cfg.get("cache_ttl_seconds", 600))
    max_links = max(1, int(cfg.get("max_links_per_message", 5)))

    log_extra = f"album_id={album_id}"
    logger.info(
        "[Netease] ════════════════════════════════════════════\n"
        "[Netease]  ⏳ 开始处理专辑 → id=%s, api=%s, hq=%s\n"
        "[Netease] ════════════════════════════════════════════",
        album_id, api_base, high_quality,
    )

    # ===== 步骤 1: 获取专辑详情和曲目列表 =====
    step_start = time.time()
    logger.info("[Netease] ▶ 专辑[1/2] 获取详情 → /album?id=%s", album_id)
    try:
        album_name, songs = await fetch_album_detail(album_id, api_base, api_timeout, real_ip)
    except Exception as e:
        elapsed = time.time() - step_start
        logger.error("[Netease] ✗ 专辑[1/2] 失败 (%.1fs) → %s | %s", elapsed, e, log_extra)
        raise

    step_elapsed = time.time() - step_start
    if not songs:
        logger.warning("[Netease] ✗ 专辑[1/2] (%.1fs) → 专辑为空, id=%s", step_elapsed, album_id)
        await bot.send(event, Message(msg("netease.not_found")))
        return

    logger.info(
        "[Netease] ✓ 专辑[1/2] (%.1fs) → 《%s》共 %d 首",
        step_elapsed, album_name, len(songs),
    )

    songs_to_process = [s for s in songs if s.id][:max_links]
    total_to_process = len(songs_to_process)

    # 发送专辑信息
    await bot.send(event, Message(
        msg("netease.album_info", album_name=album_name, song_count=total_to_process)
    ))

    # ===== 步骤 2: 逐首处理 =====
    ok_count = 0
    fail_count = 0

    for idx, song_info in enumerate(songs_to_process, 1):
        song_label = f"[{idx}/{total_to_process}]"
        logger.info(
            "[Netease] ▶ 专辑[2/2] %s %s — %s",
            song_label, song_info.name, song_info.artist,
        )

        try:
            # 2a. 获取音频 URL
            url_result = await fetch_song_url(
                song_info.id, api_base, api_timeout,
                real_ip, high_quality, cookie,
            )
            if not url_result or not url_result.url:
                logger.warning(
                    "[Netease]  ├─ ✗ %s 不可用 (版权限制) → %s — %s",
                    song_label, song_info.name, song_info.artist,
                )
                fail_count += 1
                continue

            file_ext = f".{url_result.type}" if url_result.type in ("flac", "ogg", "wav") else ".mp3"

            # 2b. 下载音频
            audio_path = await download_audio(
                url_result.url,
                cache_dir=cache_dir,
                timeout=api_timeout,
                max_file_mb=max_file_mb,
                cache_ttl_seconds=cache_ttl,
                file_ext=file_ext,
            )
            file_size_mb = audio_path.stat().st_size / 1024 / 1024
            logger.info(
                "[Netease]  ├─ ✓ %s 下载完成 → %s — %s (%.1fMB)",
                song_label, song_info.name, song_info.artist, file_size_mb,
            )

            # 2c. 发送歌曲信息文本
            await bot.send(event, Message(
                msg("netease.info", name=song_info.name, artist=song_info.artist, album=album_name)
            ))

            # 2d. 上传文件（等待上传完成后再处理下一首）
            file_name = _sanitize_filename(
                f"{song_info.artist} - {song_info.name}{file_ext}"
            )
            if isinstance(event, GroupMessageEvent):
                await bot.call_api(
                    "upload_group_file",
                    group_id=event.group_id,
                    file=str(audio_path),
                    name=file_name,
                )
            elif isinstance(event, PrivateMessageEvent):
                await bot.call_api(
                    "upload_private_file",
                    user_id=event.user_id,
                    file=str(audio_path),
                    name=file_name,
                )
            logger.info(
                "[Netease]  ├─ ✓ %s 上传完成 → %s",
                song_label, file_name,
            )
            ok_count += 1

        except Exception as e:
            logger.warning(
                "[Netease]  ├─ ✗ %s 处理失败: %s — %s: %s",
                song_label, song_info.name, song_info.artist, e,
            )
            fail_count += 1

    # ===== 完成 =====
    total_elapsed = time.time() - session_start
    logger.info(
        "[Netease] ════════════════════════════════════════════\n"
        "[Netease]  🎉 专辑处理完毕! 总耗时 %.1fs\n"
        "[Netease]  📀 %s\n"
        "[Netease]  ✅ 成功 %d 首 / ❌ 失败 %d 首 / 共 %d 首\n"
        "[Netease] ════════════════════════════════════════════",
        total_elapsed, album_name, ok_count, fail_count, total_to_process,
    )


async def _process_single_song(
    bot: Bot,
    event: MessageEvent,
    song_id: str,
    cfg: dict,
) -> None:
    """处理单个歌曲 ID 的完整流程：获取详情 → 获取 URL → 下载 → 发送。"""
    session_start = time.time()
    api_base = str(cfg.get("api_base_url", "http://127.0.0.1:3000"))
    api_timeout = int(cfg.get("api_timeout", 30))
    real_ip = str(cfg.get("real_ip", "")).strip()
    high_quality = bool(cfg.get("high_quality", True))
    cookie = str(cfg.get("cookie", "")).strip()
    cache_dir = str(cfg.get("cache_dir", "/tmp/hikari_bot/netease"))
    max_file_mb = int(cfg.get("max_file_mb", 50))
    cache_ttl = int(cfg.get("cache_ttl_seconds", 600))

    log_extra = f"song_id={song_id} api={api_base} timeout={api_timeout}s hq={high_quality} cookie={'已配置' if cookie else '未配置'}"
    logger.info("[Netease] ⏳ 开始处理歌曲 → %s", log_extra)

    # ===== 步骤 1: 获取歌曲详情 =====
    step_start = time.time()
    logger.info("[Netease] ▶ 步骤 1/4: 获取歌曲详情 → id=%s", song_id)
    try:
        song = await fetch_song_detail(song_id, api_base, api_timeout, real_ip)
    except Exception as e:
        elapsed = time.time() - step_start
        logger.error(
            "[Netease] ✗ 步骤 1/4 失败 (%.1fs) → 获取歌曲详情时异常: %s | %s",
            elapsed, e, log_extra,
        )
        raise

    step_elapsed = time.time() - step_start
    if not song or not song.name:
        logger.warning(
            "[Netease] ✗ 步骤 1/4 完成 (%.1fs) → 未找到歌曲信息, id=%s",
            step_elapsed, song_id,
        )
        await bot.send(event, Message(msg("netease.not_found")))
        return

    logger.info(
        "[Netease] ✓ 步骤 1/4 完成 (%.1fs) → %s — %s / %s",
        step_elapsed, song.name, song.artist, song.album,
    )

    # ===== 步骤 2: 获取音频 URL =====
    step_start = time.time()
    hq_label = "高音质" if high_quality else "标准"
    logger.info("[Netease] ▶ 步骤 2/4: 获取音频 URL → id=%s (%s)", song_id, hq_label)
    try:
        url_result = await fetch_song_url(song_id, api_base, api_timeout, real_ip, high_quality, cookie)
    except Exception as e:
        elapsed = time.time() - step_start
        logger.error(
            "[Netease] ✗ 步骤 2/4 失败 (%.1fs) → 获取音频 URL 时异常: %s | %s",
            elapsed, e, log_extra,
        )
        raise

    step_elapsed = time.time() - step_start
    if not url_result.url:
        logger.warning(
            "[Netease] ✗ 步骤 2/4 完成 (%.1fs) → 音频链接不可用 (版权/登录限制) | id=%s, code=%s",
            step_elapsed, song_id, url_result.code,
        )
        await bot.send(event, Message(msg("netease.url_unavailable")))
        return

    file_ext = f".{url_result.type}" if url_result.type in ("flac", "ogg", "wav") else ".mp3"
    logger.info(
        "[Netease] ✓ 步骤 2/4 完成 (%.1fs) → 获取到音频链接, br=%skbps, type=%s, size=%.1fMB",
        step_elapsed, url_result.br // 1000, file_ext, url_result.size / 1024 / 1024,
    )

    # ===== 步骤 3: 下载音频 =====
    step_start = time.time()
    logger.info(
        "[Netease] ▶ 步骤 3/4: 下载音频 → id=%s, type=%s, max_size=%dMB",
        song_id, file_ext, max_file_mb,
    )
    try:
        audio_path = await download_audio(
            url_result.url,
            cache_dir=cache_dir,
            timeout=api_timeout,
            max_file_mb=max_file_mb,
            cache_ttl_seconds=cache_ttl,
            file_ext=file_ext,
        )
    except Exception as e:
        elapsed = time.time() - step_start
        logger.error(
            "[Netease] ✗ 步骤 3/4 失败 (%.1fs) → 下载音频时异常: %s | %s",
            elapsed, e, log_extra,
        )
        raise

    step_elapsed = time.time() - step_start
    file_size = audio_path.stat().st_size
    logger.info(
        "[Netease] ✓ 步骤 3/4 完成 (%.1fs) → 音频文件: %s (%.1fMB)",
        step_elapsed, audio_path.name, file_size / 1024 / 1024,
    )

    # ===== 步骤 4: 发送音频 =====
    step_start = time.time()
    logger.info("[Netease] ▶ 步骤 4/4: 发送音频 → id=%s", song_id)
    try:
        await send_song(bot, event, song, audio_path, cfg)
    except Exception as e:
        elapsed = time.time() - step_start
        logger.error(
            "[Netease] ✗ 步骤 4/4 失败 (%.1fs) → 发送音频时异常: %s | %s",
            elapsed, e, log_extra,
        )
        raise

    step_elapsed = time.time() - step_start
    total_elapsed = time.time() - session_start
    logger.info(
        "[Netease] ✓ 步骤 4/4 完成 (%.1fs) | "
        "🎉 全部完成 (总耗时 %.1fs) → %s — %s",
        step_elapsed, total_elapsed, song.name, song.artist,
    )
