"""
网易云音乐解析插件处理逻辑。

包含队列条目处理和单曲/播客/专辑的完整解析流程。
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Optional

from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent
from nonebot.adapters.onebot.v11 import NetworkError

from core.activity_tracker import ActivityScope
from core.bot_messages import get_message as msg
from core.error_notifier import notify_error_to_superuser, send_user_error
from core.stats_tracker import increment as stats_increment

from .config import get_config
from .downloader import download_audio
from .api import (
    fetch_album_detail,
    fetch_playlist_detail,
    fetch_program_detail,
    fetch_song_detail,
    fetch_song_url,
)
from .packer import pack_to_zip
from .prefs import record_send
from .sender import send_song
from . import NeteaseQueueItem, _sanitize_filename

logger = logging.getLogger("HikariBot.NeteasePlugin")


def _is_upload_timeout(exc: Exception) -> bool:
    """判断是否为上传超时错误（WS 响应超时，文件很可能已送达，不应重试）。

    上传文件时 OneBot WS 调用超时：NapCat 通常已收到文件并完成发送，
    只是响应没能及时返回，重试会导致同一文件重复发送。
    """
    if not isinstance(exc, NetworkError):
        return False
    msg_text = str(exc.msg or "")
    return (
        ("upload_private_file" in msg_text or "upload_group_file" in msg_text)
        and "timeout" in msg_text
    )


async def _notify_final_failure(
    bot: Bot,
    event: MessageEvent,
    label: str,
    e: Exception,
    note: str,
) -> None:
    """终结失败：记录日志并通知 superuser。"""
    logger.exception("[Netease] ✗ %s %s → %s", label, note, e)
    try:
        await send_user_error(bot, event)
        await notify_error_to_superuser(bot, event, e, "NeteaseParser")
    except Exception as notify_err:
        logger.exception("发送错误通知失败: %s", notify_err)


async def _process_queue_item(item: NeteaseQueueItem, cfg: dict) -> None:
    """执行单个队列条目（歌曲或播客），带重试。"""
    # 条目未显式指定格式时，按发送者偏好决定（无偏好 → 默认 FLAC）
    quality = item.quality if item.quality in ("mp3", "flac") else "auto"
    if quality == "auto":
        from .prefs import get_user_quality

        quality = get_user_quality(item.event.get_user_id())

    label = f"{item.item_type}_{item.item_id} (quality={quality})"
    retry_count = max(0, int(cfg.get("parse_retry_count", 2)))
    retry_delay = max(0.0, float(cfg.get("parse_retry_delay_seconds", 2.0)))
    max_attempts = retry_count + 1

    for attempt in range(1, max_attempts + 1):
        try:
            with ActivityScope(
                "netease_parser",
                "parsing",
                f"解析网易云{item.item_type}",
                description=f"{item.item_type}={item.item_id} quality={quality}",
            ):
                if item.item_type == "program":
                    await _process_single_program(item.bot, item.event, item.item_id, cfg, quality)
                elif item.item_type == "album":
                    await _process_single_album(item.bot, item.event, item.item_id, cfg, quality)
                elif item.item_type == "playlist":
                    await _process_single_playlist(item.bot, item.event, item.item_id, cfg, quality)
                else:
                    await _process_single_song(item.bot, item.event, item.item_id, cfg, quality)
            stats_increment(item.event, "netease_parsed", 1)
            return  # 成功，不重试
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if _is_upload_timeout(e):
                # 上传超时：文件很可能已送达，重试会重复发送，直接终结
                await _notify_final_failure(
                    item.bot, item.event, label, e,
                    "上传超时（文件可能已送达），不重试",
                )
                return
            if attempt < max_attempts:
                logger.warning(
                    "[Netease] 重试 %d/%d → %s error=%s",
                    attempt, retry_count, label, e,
                )
                await asyncio.sleep(retry_delay)
            else:
                await _notify_final_failure(
                    item.bot, item.event, label, e,
                    f"重试耗尽 (共 {max_attempts} 次)",
                )


async def _process_single_program(
    bot: Bot,
    event: MessageEvent,
    program_id: str,
    cfg: dict,
    quality: str = "auto",
) -> None:
    """处理播客/电台节目：获取节目详情 → 提取 mainSong ID → 获取音频 URL → 下载 → 发送。"""
    session_start = time.time()
    api_base = str(cfg.get("api_base_url", "http://127.0.0.1:3000"))
    api_timeout = int(cfg.get("api_timeout", 30))
    real_ip = str(cfg.get("real_ip", "")).strip()
    high_quality = _resolve_high_quality(quality, cfg)
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
        message_ids = await send_song(bot, event, program, audio_path, cfg)
        record_send(
            event.get_user_id(),
            item_type="program",
            item_id=program_id,
            title=program.name,
            quality=_sent_quality(high_quality),
            message_ids=message_ids,
        )
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


async def _upload_file_via_bot(
    bot: Bot,
    event: MessageEvent,
    file_path: Path,
    file_name: str,
) -> str:
    """通过 Bot API 上传文件（通用方法），返回 message_id（拿不到时为空串）。"""
    if isinstance(event, GroupMessageEvent):
        resp = await bot.call_api(
            "upload_group_file",
            group_id=event.group_id,
            file=str(file_path),
            name=file_name,
        )
        return _extract_message_id(resp)
    elif isinstance(event, PrivateMessageEvent):
        resp = await bot.call_api(
            "upload_private_file",
            user_id=event.user_id,
            file=str(file_path),
            name=file_name,
        )
        return _extract_message_id(resp)
    else:
        from nonebot.adapters.onebot.v11 import MessageSegment

        uri = file_path.resolve().as_uri()
        result = await bot.send(event, Message(MessageSegment.record(uri)))
        return _extract_message_id(result)


def _extract_message_id(result: Any) -> str:
    """从 bot.send / call_api 返回值中提取 message_id（拿不到时返回空串）。"""
    if isinstance(result, dict):
        mid = result.get("message_id") or result.get("msg_id")
        if mid:
            return str(mid)
    if result is None:
        return ""
    text = str(result)
    return text if text.isdigit() else ""


def _resolve_high_quality(quality: str, cfg: dict) -> bool:
    """解析格式参数为 high_quality 布尔值。

    quality: "mp3" → False（320k）; "flac" → True（999k）; 其它 → 回退配置。
    """
    if quality == "mp3":
        return False
    if quality == "flac":
        return True
    return bool(cfg.get("high_quality", True))


def _sent_quality(high_quality: bool) -> str:
    """high_quality → 记录用格式名。"""
    return "flac" if high_quality else "mp3"


async def _download_single_song_for_batch(
    song_info: "NeteaseSongInfo",
    album_name: str,
    cfg: dict,
    quality: str = "auto",
) -> tuple[Optional[Path], str]:
    """
    下载单首歌曲，返回 (本地路径, 展示文件名)。

    Returns:
        (Path, filename) 下载成功
        (None, reason) 下载失败
    """
    api_base = str(cfg.get("api_base_url", "http://127.0.0.1:3000"))
    api_timeout = int(cfg.get("api_timeout", 30))
    real_ip = str(cfg.get("real_ip", "")).strip()
    high_quality = _resolve_high_quality(quality, cfg)
    cookie = str(cfg.get("cookie", "")).strip()
    cache_dir = str(cfg.get("cache_dir", "/tmp/hikari_bot/netease"))
    max_file_mb = int(cfg.get("max_file_mb", 200))
    cache_ttl = int(cfg.get("cache_ttl_seconds", 600))

    try:
        url_result = await fetch_song_url(
            song_info.id, api_base, api_timeout,
            real_ip, high_quality, cookie,
        )
        if not url_result or not url_result.url:
            logger.warning(
                "[Netease]   跳过（无 URL）→ %s — %s", song_info.name, song_info.artist,
            )
            return None, "无可用音频链接"

        file_ext = f".{url_result.type}" if url_result.type in ("flac", "ogg", "wav") else ".mp3"
        audio_path = await download_audio(
            url_result.url,
            cache_dir=cache_dir,
            timeout=api_timeout,
            max_file_mb=max_file_mb,
            cache_ttl_seconds=cache_ttl,
            file_ext=file_ext,
        )
        display_name = _sanitize_filename(
            f"{song_info.artist} - {song_info.name}{file_ext}"
        )
        logger.info(
            "[Netease]   ✓ 下载完成 → %s — %s", song_info.name, song_info.artist,
        )
        return audio_path, display_name
    except Exception as e:
        logger.warning(
            "[Netease]   下载失败 → %s — %s: %s", song_info.name, song_info.artist, e,
        )
        return None, str(e)


async def _process_multi_file_sequential(
    bot: Bot,
    event: MessageEvent,
    title: str,
    songs: list,
    cfg: dict,
    item_type: str = "album",
    quality: str = "auto",
) -> None:
    """
    顺序逐首处理多文件：每首歌 → 发信息 → 上传文件 → 下一首。

    保证顺序：信息-文件-信息-文件-... 不会乱序。
    """
    session_start = time.time()
    high_quality = _resolve_high_quality(quality, cfg)
    max_links = max(1, int(cfg.get("max_links_per_message", 5)))
    songs_to_process = [s for s in songs if s.id][:max_links]
    total = len(songs_to_process)

    ok_count = 0
    fail_count = 0

    for idx, song_info in enumerate(songs_to_process, 1):
        label = f"[{idx}/{total}]"
        logger.info("[Netease] ▶ %s %s — %s", label, song_info.name, song_info.artist)

        try:
            audio_path, display_name = await _download_single_song_for_batch(
                song_info, title, cfg, quality,
            )
            if audio_path is None:
                fail_count += 1
                continue

            # 发送信息文本
            info_text = msg(
                "netease.info",
                name=song_info.name, artist=song_info.artist, album=title,
            )
            if bool(cfg.get("quality_switch", True)):
                info_text += "\n" + msg("netease.format_hint")
            result = await bot.send(event, Message(info_text))
            message_ids = [m for m in [_extract_message_id(result)] if m]

            # 上传文件
            mid = await _upload_file_via_bot(bot, event, audio_path, display_name)
            if mid:
                message_ids.append(mid)
            record_send(
                event.get_user_id(),
                item_type="song",
                item_id=song_info.id,
                title=song_info.name,
                quality=_sent_quality(high_quality),
                message_ids=message_ids,
            )
            logger.info("[Netease]   ✓ %s 上传完成 → %s", label, display_name)
            ok_count += 1

        except Exception as e:
            logger.warning("[Netease]   ✗ %s 处理失败: %s: %s", label, song_info.name, e)
            fail_count += 1

    total_elapsed = time.time() - session_start
    logger.info(
        "[Netease] 🎉 %s 处理完毕! 耗时 %.1fs, ✅ %d / ❌ %d / 共 %d",
        title, total_elapsed, ok_count, fail_count, total,
    )


async def _process_multi_file_zip(
    bot: Bot,
    event: MessageEvent,
    title: str,
    songs: list,
    cfg: dict,
    item_type: str = "album",
    item_id: str = "",
    quality: str = "auto",
) -> None:
    """
    多文件 ZIP 模式：下载所有歌曲 → 打包 ZIP → 发送 ZIP。
    """
    session_start = time.time()
    high_quality = _resolve_high_quality(quality, cfg)
    max_links = max(1, int(cfg.get("max_links_per_message", 100)))
    send_strategy = cfg.get("send_strategy", {})
    zip_max_files = int(send_strategy.get("zip_max_files", 50))
    zip_max_mb = int(send_strategy.get("zip_max_mb", 200))
    cache_dir = str(cfg.get("cache_dir", "/tmp/hikari_bot/netease"))
    cache_ttl = int(cfg.get("cache_ttl_seconds", 600))

    songs_to_process = [s for s in songs if s.id][:max_links]
    total = len(songs_to_process)

    logger.info(
        "[Netease] ⏳ %s ZIP 模式: 共 %d 首, 开始下载...",
        title, total,
    )

    # 下载所有歌曲（并发限制为 3）
    sem = asyncio.Semaphore(3)
    downloaded: list[tuple[Path, str]] = []
    download_ok = 0
    download_fail = 0

    async def _dl_one(song_info: "NeteaseSongInfo") -> None:
        nonlocal download_ok, download_fail
        async with sem:
            audio_path, display_name = await _download_single_song_for_batch(
                song_info, title, cfg, quality,
            )
            if audio_path is not None:
                downloaded.append((audio_path, display_name))
                download_ok += 1
            else:
                download_fail += 1

    tasks = [_dl_one(s) for s in songs_to_process]
    await asyncio.gather(*tasks)
    logger.info(
        "[Netease] %s 下载完成: ✅ %d / ❌ %d / 共 %d",
        title, download_ok, download_fail, total,
    )

    if not downloaded:
        await bot.send(event, Message(msg("netease.pack_failed")))
        logger.error("[Netease] %s 无文件可打包", title)
        return

    # 打包为 ZIP
    zip_name = _sanitize_filename(title) or f"netease_{item_type}"
    zip_paths = await pack_to_zip(
        files=downloaded,
        zip_name=zip_name,
        output_dir=cache_dir,
        max_files=zip_max_files,
        max_size_mb=zip_max_mb,
        cache_ttl_seconds=cache_ttl,
    )

    # 发送 ZIP 文件
    message_ids: list[str] = []
    for zip_path in zip_paths:
        zip_display = f"{zip_name}.zip" if len(zip_paths) == 1 else zip_path.name
        pack_msg = Message(MessageSegment.reply(event.message_id)) + msg(
            "netease.pack_info", name=zip_display, count=len(downloaded),
        )
        if bool(cfg.get("quality_switch", True)):
            pack_msg += Message("\n" + msg("netease.format_hint"))
        result = await bot.send(event, pack_msg)
        mid = _extract_message_id(result)
        if mid:
            message_ids.append(mid)
        mid = await _upload_file_via_bot(bot, event, zip_path, zip_path.name)
        if mid:
            message_ids.append(mid)

    record_send(
        event.get_user_id(),
        item_type=item_type,
        item_id=item_id,
        title=title,
        quality=_sent_quality(high_quality),
        message_ids=message_ids,
    )

    total_elapsed = time.time() - session_start
    logger.info(
        "[Netease] 🎉 %s ZIP 处理完毕! 耗时 %.1fs, ZIP=%d 个, 文件=%d 首",
        title, total_elapsed, len(zip_paths), len(downloaded),
    )


async def _process_single_album(
    bot: Bot,
    event: MessageEvent,
    album_id: str,
    cfg: dict,
    quality: str = "auto",
) -> None:
    """
    处理专辑：
    - sequential 模式：逐首获取 → 下载 → 发送信息 → 上传文件
    - zip 模式（默认）：下载所有歌曲 → 打包 ZIP → 发送 ZIP
    """
    session_start = time.time()
    api_base = str(cfg.get("api_base_url", "http://127.0.0.1:3000"))
    api_timeout = int(cfg.get("api_timeout", 30))
    real_ip = str(cfg.get("real_ip", "")).strip()

    send_strategy = cfg.get("send_strategy", {})
    multi_file_mode = send_strategy.get("multi_file_mode", "sequential")

    log_extra = f"album_id={album_id}"
    logger.info(
        "[Netease] ════════════════════════════════════════════\n"
        "[Netease]  ⏳ 开始处理专辑 → id=%s, mode=%s\n"
        "[Netease] ════════════════════════════════════════════",
        album_id, multi_file_mode,
    )

    # 获取专辑详情
    step_start = time.time()
    logger.info("[Netease] ▶ 专辑 获取详情 → /album?id=%s", album_id)
    try:
        album_name, songs = await fetch_album_detail(album_id, api_base, api_timeout, real_ip)
    except Exception as e:
        elapsed = time.time() - step_start
        logger.error("[Netease] ✗ 专辑详情失败 (%.1fs) → %s | %s", elapsed, e, log_extra)
        raise

    step_elapsed = time.time() - step_start
    if not songs:
        logger.warning("[Netease] ✗ 专辑为空, id=%s (%.1fs)", album_id, step_elapsed)
        await bot.send(event, Message(msg("netease.not_found")))
        return

    logger.info(
        "[Netease] ✓ 专辑详情 (%.1fs) → 《%s》共 %d 首",
        step_elapsed, album_name, len(songs),
    )

    max_links = max(1, int(cfg.get("max_links_per_message", 5)))
    songs_to_process = [s for s in songs if s.id][:max_links]

    if multi_file_mode == "zip":
        await _process_multi_file_zip(
            bot, event, album_name, songs_to_process, cfg, "album", album_id, quality,
        )
    else:
        await _process_multi_file_sequential(
            bot, event, album_name, songs_to_process, cfg, "album", quality,
        )

    total_elapsed = time.time() - session_start
    logger.info(
        "[Netease] 🎉 专辑处理完毕 (总耗时 %.1fs) → 《%s》",
        total_elapsed, album_name,
    )


async def _process_single_playlist(
    bot: Bot,
    event: MessageEvent,
    playlist_id: str,
    cfg: dict,
    quality: str = "auto",
) -> None:
    """
    处理歌单：
    - sequential 模式：逐首获取 → 下载 → 发送信息 → 上传文件
    - zip 模式（默认）：下载所有歌曲 → 打包 ZIP → 发送 ZIP
    """
    session_start = time.time()
    api_base = str(cfg.get("api_base_url", "http://127.0.0.1:3000"))
    api_timeout = int(cfg.get("api_timeout", 30))
    real_ip = str(cfg.get("real_ip", "")).strip()

    send_strategy = cfg.get("send_strategy", {})
    multi_file_mode = send_strategy.get("multi_file_mode", "sequential")

    log_extra = f"playlist_id={playlist_id}"
    logger.info(
        "[Netease] ════════════════════════════════════════════\n"
        "[Netease]  ⏳ 开始处理歌单 → id=%s, mode=%s\n"
        "[Netease] ════════════════════════════════════════════",
        playlist_id, multi_file_mode,
    )

    # 获取歌单详情
    step_start = time.time()
    logger.info("[Netease] ▶ 歌单 获取详情 → /playlist/detail?id=%s", playlist_id)
    try:
        playlist_name, songs = await fetch_playlist_detail(
            playlist_id, api_base, api_timeout, real_ip,
        )
    except Exception as e:
        elapsed = time.time() - step_start
        logger.error("[Netease] ✗ 歌单详情失败 (%.1fs) → %s | %s", elapsed, e, log_extra)
        raise

    step_elapsed = time.time() - step_start
    if not songs:
        logger.warning("[Netease] ✗ 歌单为空, id=%s (%.1fs)", playlist_id, step_elapsed)
        await bot.send(event, Message(msg("netease.not_found")))
        return

    logger.info(
        "[Netease] ✓ 歌单详情 (%.1fs) → 《%s》共 %d 首",
        step_elapsed, playlist_name, len(songs),
    )

    # 发送歌单信息
    max_links = max(1, int(cfg.get("max_links_per_message", 100)))
    songs_to_process = [s for s in songs if s.id][:max_links]
    await bot.send(event, Message(
        msg("netease.playlist_info", playlist_name=playlist_name, song_count=len(songs_to_process))
    ))

    if multi_file_mode == "zip":
        await _process_multi_file_zip(
            bot, event, playlist_name, songs_to_process, cfg, "playlist", playlist_id, quality,
        )
    else:
        await _process_multi_file_sequential(
            bot, event, playlist_name, songs_to_process, cfg, "playlist", quality,
        )

    total_elapsed = time.time() - session_start
    logger.info(
        "[Netease] 🎉 歌单处理完毕 (总耗时 %.1fs) → 《%s》",
        total_elapsed, playlist_name,
    )


async def _process_single_song(
    bot: Bot,
    event: MessageEvent,
    song_id: str,
    cfg: dict,
    quality: str = "auto",
) -> None:
    """处理单个歌曲 ID 的完整流程：获取详情 → 获取 URL → 下载 → 发送。"""
    session_start = time.time()
    api_base = str(cfg.get("api_base_url", "http://127.0.0.1:3000"))
    api_timeout = int(cfg.get("api_timeout", 30))
    real_ip = str(cfg.get("real_ip", "")).strip()
    high_quality = _resolve_high_quality(quality, cfg)
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
        message_ids = await send_song(bot, event, song, audio_path, cfg)
        record_send(
            event.get_user_id(),
            item_type="song",
            item_id=song_id,
            title=song.name,
            quality=_sent_quality(high_quality),
            message_ids=message_ids,
        )
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
