"""
网易云音乐解析插件入口。

NoneBot 加载此插件时自动注册：
1. 自动 URL 检测 handler → 注册到 message_pipeline
2. 检测 music.163.com / 163cn.tv 歌曲链接 → API 获取 FLAC/MP3 → 下载 → 发送

队列行为：多个链接通过 asyncio.Queue 排队，后台 worker 并发处理
（与 media_parser 同样的队列模式）。
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nonebot.adapters.onebot.v11 import (
    Bot,
    Event,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent

from core.access_control import is_event_allowed
from core.activity_tracker import ActivityScope
from core.bot_messages import get_message as msg
from core.error_notifier import notify_error_to_superuser, send_user_error
from core.message_pipeline import register_handler
from core.stats_tracker import increment as stats_increment

from .config import get_config
from .downloader import download_audio
from .parser import (
    NeteaseSongInfo,
    extract_album_ids_from_event,
    extract_program_ids_from_event,
    extract_song_ids_from_event,
    fetch_album_detail,
    fetch_program_detail,
    fetch_song_detail,
    fetch_song_url,
    has_netease_url,
)
from .sender import send_song

logger = logging.getLogger("HikariBot.NeteasePlugin")

# 触发首次加载并输出配置摘要
get_config()

# ── 后台队列 ──


@dataclass
class NeteaseQueueItem:
    """单个网易云解析队列条目。"""
    bot: Bot
    event: MessageEvent
    item_id: str
    item_type: str  # "song", "program", 或 "album"


_parse_queue: asyncio.Queue[NeteaseQueueItem] | None = None
_parse_worker_tasks: set[asyncio.Task[None]] = set()


def _queue_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    """从配置中提取队列设置。"""
    raw = cfg.get("parse_queue") if isinstance(cfg.get("parse_queue"), dict) else {}
    return {
        "enabled": bool(raw.get("enabled", True)),
        "max_size": max(1, int(raw.get("max_size", 100))),
        "max_concurrent": max(1, int(raw.get("max_concurrent", 2))),
        "delay_seconds": max(0.0, float(raw.get("delay_seconds", 0.8))),
    }


def _ensure_parse_workers(cfg: dict[str, Any]) -> asyncio.Queue[NeteaseQueueItem]:
    """确保有足够的后台 worker 在运行。"""
    global _parse_queue
    settings = _queue_settings(cfg)
    if _parse_queue is None:
        _parse_queue = asyncio.Queue(maxsize=settings["max_size"])
    alive = {task for task in _parse_worker_tasks if not task.done()}
    _parse_worker_tasks.clear()
    _parse_worker_tasks.update(alive)
    while len(_parse_worker_tasks) < settings["max_concurrent"]:
        worker_no = len(_parse_worker_tasks) + 1
        task = asyncio.create_task(
            _parse_worker(),
            name=f"HikariNeteaseQueue-{worker_no}",
        )
        _parse_worker_tasks.add(task)
        task.add_done_callback(_parse_worker_tasks.discard)
    return _parse_queue


async def _parse_worker() -> None:
    """后台 worker：消费队列中的解析任务。"""
    logger.info("[Netease] 解析队列 worker 已启动")
    while True:
        assert _parse_queue is not None
        item = await _parse_queue.get()
        try:
            cfg = get_config()
            await _process_queue_item(item, cfg)
            delay = _queue_settings(cfg)["delay_seconds"]
            if delay > 0:
                await asyncio.sleep(delay)
        except Exception as e:
            logger.exception("[Netease] 队列任务异常: %s", e)
            try:
                await send_user_error(item.bot, item.event)
                await notify_error_to_superuser(item.bot, item.event, e, "NeteaseParser")
            except Exception as notify_err:
                logger.exception("发送错误通知失败: %s", notify_err)
        finally:
            _parse_queue.task_done()


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


async def _enqueue_parse_jobs(
    bot: Bot,
    event: MessageEvent,
    song_ids: list[str],
    program_ids: list[str],
) -> None:
    """将歌曲/播客 ID 加入解析队列。"""
    cfg = get_config()
    settings = _queue_settings(cfg)

    # 收集所有条目
    items: list[NeteaseQueueItem] = []
    for pid in program_ids:
        items.append(NeteaseQueueItem(bot=bot, event=event, item_id=pid, item_type="program"))
    for sid in song_ids:
        items.append(NeteaseQueueItem(bot=bot, event=event, item_id=sid, item_type="song"))

    if not items:
        logger.info("[Netease] 未提取到任何歌曲/播客 ID，跳过处理")
        return

    # 队列禁用 → 同步直接处理（用于少量链接）
    if not settings["enabled"]:
        for queued_item in items:
            await _process_queue_item(queued_item, get_config())
        return

    queue = _ensure_parse_workers(cfg)

    queued = 0
    dropped = 0
    for queued_item in items:
        if queue.full():
            dropped += 1
            continue
        queue.put_nowait(queued_item)
        queued += 1

    logger.info(
        "[Netease] 入队完成 → 入队=%d, 丢弃=%d, 队列大小=%d",
        queued, dropped, queue.qsize(),
    )
    if dropped:
        logger.warning("[Netease] 解析队列已满，%d 个链接被丢弃", dropped)


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
    处理专辑：获取专辑曲目列表 → 并发下载所有歌曲 → 逐个上传文件 → 合并转发曲目总结。
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

    log_extra = f"album_id={album_id} api={api_base} max_links={max_links}"
    logger.info(
        "[Netease] ════════════════════════════════════════════\n"
        "[Netease]  ⏳ 开始处理专辑 → id=%s, api=%s, hq=%s, cookie=%s\n"
        "[Netease]  ⏳ 设置: max_links=%d, max_file=%dMB, cache_ttl=%ds\n"
        "[Netease] ════════════════════════════════════════════",
        album_id, api_base, high_quality,
        "已配置" if cookie else "未配置",
        max_links, max_file_mb, cache_ttl,
    )

    # ===== 步骤 1: 获取专辑详情和曲目列表 =====
    step_start = time.time()
    logger.info("[Netease] ▶ [1/3] 获取专辑详情 → 请求 API: /album?id=%s", album_id)
    try:
        album_name, songs = await fetch_album_detail(album_id, api_base, api_timeout, real_ip)
    except Exception as e:
        elapsed = time.time() - step_start
        logger.error("[Netease] ✗ 步骤 1/3 失败 (%.1fs) → %s | %s", elapsed, e, log_extra)
        raise

    step_elapsed = time.time() - step_start
    if not songs:
        logger.warning("[Netease] ✗ [1/3] 失败 (%.1fs) → 专辑为空或未找到, id=%s", step_elapsed, album_id)
        await bot.send(event, Message(msg("netease.not_found")))
        return

    logger.info(
        "[Netease] ✓ [1/3] API 返回 %d 首歌曲 (%.1fs) → 专辑《%s》",
        len(songs), step_elapsed, album_name,
    )

    # 截取前 max_links 首
    songs_to_process = [s for s in songs if s.id][:max_links]
    total_to_process = len(songs_to_process)

    # 发送专辑信息
    await bot.send(event, Message(
        msg("netease.album_info", album_name=album_name, song_count=total_to_process)
    ))

    # ===== 步骤 2: 并发下载所有歌曲 =====
    step_start = time.time()
    concurrency = min(total_to_process, 3)  # 最多 3 个并发下载
    logger.info(
        "[Netease] ▶ [2/3] 并发下载 %d 首歌曲（并发数=%d, 格式=%s）",
        total_to_process, concurrency,
        "FLAC(优先)" if high_quality else "MP3 320k",
    )

    done_count = 0
    fail_count = 0
    total_bytes = 0
    lock = asyncio.Lock()

    sem = asyncio.Semaphore(concurrency)

    async def _download_one(song_info: NeteaseSongInfo) -> tuple[NeteaseSongInfo, Path | None]:
        """下载单首歌曲，返回 (song_info, audio_path)。"""
        nonlocal done_count, fail_count, total_bytes
        async with sem:
            try:
                # 获取音频 URL
                url_result = await fetch_song_url(
                    song_info.id, api_base, api_timeout,
                    real_ip, high_quality, cookie,
                )
                if not url_result or not url_result.url:
                    logger.warning(
                        "[Netease]  ├─ ✗ 不可用: %s — %s (id=%s, 可能受版权限制)",
                        song_info.name, song_info.artist, song_info.id,
                    )
                    async with lock:
                        fail_count += 1
                    return song_info, None

                file_ext = f".{url_result.type}" if url_result.type in ("flac", "ogg", "wav") else ".mp3"

                # 下载
                audio_path = await download_audio(
                    url_result.url,
                    cache_dir=cache_dir,
                    timeout=api_timeout,
                    max_file_mb=max_file_mb,
                    cache_ttl_seconds=cache_ttl,
                    file_ext=file_ext,
                )
                file_size_mb = audio_path.stat().st_size / 1024 / 1024
                async with lock:
                    done_count += 1
                    total_bytes += audio_path.stat().st_size
                logger.info(
                    "[Netease]  ├─ ✓ [%d/%d] %s — %s (%.1fMB, %s)",
                    done_count, total_to_process,
                    song_info.name, song_info.artist,
                    file_size_mb, url_result.type.upper(),
                )
                return song_info, audio_path
            except Exception as e:
                logger.warning(
                    "[Netease]  ├─ ✗ 下载失败: %s — %s: %s",
                    song_info.name, song_info.artist, e,
                )
                async with lock:
                    fail_count += 1
                return song_info, None

    # 并发下载所有歌曲
    download_results = await asyncio.gather(*[
        _download_one(s) for s in songs_to_process
    ])

    # 过滤出成功下载的
    downloaded = [(s, p) for s, p in download_results if p is not None]
    download_count = len(downloaded)
    step_elapsed = time.time() - step_start
    total_size_mb = total_bytes / 1024 / 1024
    logger.info(
        "[Netease] ✓ [2/3] 下载完成 (%.1fs) → 成功 %d 首 / 失败 %d 首 / 总计 %.1fMB",
        step_elapsed, download_count, fail_count, total_size_mb,
    )

    if not downloaded:
        logger.warning("[Netease] 专辑歌曲全部下载失败")
        await bot.send(event, Message(msg("netease.failed")))
        return

    # ===== 步骤 3: 分批上传所有文件 =====
    step_start = time.time()
    upload_count = len(downloaded)
    batch_size = 3
    logger.info(
        "[Netease] ▶ [3/3] 上传 %d 个音频文件到群文件（每批 %d 个, 间隔 0.5s）",
        upload_count, batch_size,
    )

    for batch_idx in range(0, upload_count, batch_size):
        batch = downloaded[batch_idx:batch_idx + batch_size]
        batch_tasks = []
        for song_info, audio_path in batch:
            file_name = _sanitize_filename(
                f"{song_info.artist} - {song_info.name}{audio_path.suffix}"
            )
            file_size_mb = audio_path.stat().st_size / 1024 / 1024 if audio_path.exists() else 0
            if isinstance(event, GroupMessageEvent):
                batch_tasks.append(bot.call_api(
                    "upload_group_file",
                    group_id=event.group_id,
                    file=str(audio_path),
                    name=file_name,
                ))
            elif isinstance(event, PrivateMessageEvent):
                batch_tasks.append(bot.call_api(
                    "upload_private_file",
                    user_id=event.user_id,
                    file=str(audio_path),
                    name=file_name,
                ))
            logger.info(
                "[Netease]  ├─ 上传队列: %s — %s (%.1fMB → %s)",
                song_info.name, song_info.artist, file_size_mb, file_name,
            )

        await asyncio.gather(*batch_tasks)
        batch_end = min(batch_idx + batch_size, upload_count)
        logger.info(
            "[Netease]  ├─ ✓ 第 %d-%d 首上传完成",
            batch_idx + 1, batch_end,
        )
        if batch_end < upload_count:
            await asyncio.sleep(0.5)

    step_elapsed = time.time() - step_start
    total_elapsed = time.time() - session_start
    total_size_mb = sum(
        p.stat().st_size for _, p in downloaded if p and p.exists()
    ) / 1024 / 1024
    logger.info(
        "[Netease] ✓ [3/3] 上传完成 (%.1fs)\n"
        "[Netease] ════════════════════════════════════════════\n"
        "[Netease]  🎉 专辑全部处理完毕! 总耗时 %.1fs\n"
        "[Netease]  📀 专辑: %s\n"
        "[Netease]  ✅ 成功: %d / %d 首\n"
        "[Netease]  💾 总计: %.1fMB\n"
        "[Netease] ════════════════════════════════════════════",
        step_elapsed, total_elapsed,
        album_name, upload_count, total_to_process, total_size_mb,
    )

    # 发送合并转发曲目总结
    try:
        await _send_album_summary_forward(bot, event, album_name, download_results, total_to_process)
    except Exception as e:
        logger.warning("[Netease] 发送专辑总结合并转发失败: %s", e)


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


def _sanitize_filename(text: str) -> str:
    """清理文件名中的非法字符。"""
    return "".join(c for c in text if c.isprintable() and c not in r'<>:"/\|?*').strip()


async def _send_album_summary_forward(
    bot: Bot,
    event: Event,
    album_name: str,
    download_results: list[tuple[NeteaseSongInfo, Path | None]],
    total_requested: int,
) -> None:
    """
    发送合并转发消息，列出专辑所有歌曲的上传状态。
    """
    # 构建 node 列表
    nodes: list[MessageSegment] = []
    success = sum(1 for _, p in download_results if p is not None)
    failed = total_requested - success

    # 标题节点
    title_text = f"📀 专辑《{album_name}》\n共 {total_requested} 首，成功 {success} 首"
    if failed:
        title_text += f"，失败 {failed} 首"
    nodes.append(MessageSegment.node_custom(
        user_id=int(bot.self_id),
        nickname="网易云音乐",
        content=Message([
            MessageSegment.text(title_text),
        ]),
    ))

    # 每首歌一个节点
    for i, (song_info, audio_path) in enumerate(download_results, 1):
        if audio_path:
            file_size = audio_path.stat().st_size if audio_path.exists() else 0
            size_str = f"{file_size / 1024 / 1024:.1f}MB" if file_size > 0 else ""
            status = f"✅ {song_info.name} — {song_info.artist}"
            if size_str:
                status += f"（{size_str}）"
        else:
            status = f"❌ {song_info.name} — {song_info.artist}"

        nodes.append(MessageSegment.node_custom(
            user_id=int(bot.self_id),
            nickname="网易云音乐",
            content=Message([MessageSegment.text(f"{i:02d}. {status}")]),
        ))

    # 发送合并转发
    try:
        if isinstance(event, GroupMessageEvent):
            await bot.send_group_forward_msg(group_id=event.group_id, messages=nodes)
        elif isinstance(event, PrivateMessageEvent):
            await bot.send_private_forward_msg(user_id=event.user_id, messages=nodes)
        logger.info("[Netease] 专辑总结合并转发已发送 → %d 个节点", len(nodes))
    except Exception as e:
        logger.warning("[Netease] 发送专辑总结合并转发失败: %s", e)


async def _enqueue_album_parse_job(
    bot: Bot,
    event: MessageEvent,
    album_id: str,
    cfg: dict,
) -> None:
    """将专辑 ID 加入解析队列。"""
    settings = _queue_settings(cfg)

    if settings["enabled"]:
        queue = _ensure_parse_workers(cfg)
        item = NeteaseQueueItem(bot=bot, event=event, item_id=album_id, item_type="album")
        if queue.full():
            logger.warning("[Netease] 解析队列已满，专辑 %s 被丢弃", album_id)
            return
        queue.put_nowait(item)
        logger.info("[Netease] 专辑加入解析队列 → id=%s, 队列大小=%d", album_id, queue.qsize())
    else:
        # 队列禁用，直接处理
        await _process_single_album(bot, event, album_id, get_config())


class AutoNeteaseHandler:
    """自动检测网易云音乐歌曲链接并解析的 Handler。"""

    name = "NeteaseParser"

    async def match(self, event: MessageEvent, text: str) -> bool:
        cfg = get_config()
        if not cfg.get("auto_parse", True):
            return False
        if not is_event_allowed(cfg, event):
            return False

        # 检查正文是否包含网易云链接
        if has_netease_url(text):
            logger.debug("[Netease] match ✓ 正文命中 → text=%s...", text[:80])
            return True

        # 检查 QQ 卡片元数据是否包含网易云链接
        from .parser import extract_all_urls

        card_urls = extract_all_urls(event)
        for url in card_urls:
            if has_netease_url(url):
                logger.debug(
                    "[Netease] match ✓ 卡片元数据命中 → url=%s", url[:80],
                )
                return True

        return False

    async def handle(self, bot: Bot, event: MessageEvent) -> None:
        cfg = get_config()
        if not is_event_allowed(cfg, event):
            return

        max_links = max(1, int(cfg.get("max_links_per_message", 5)))
        program_ids = (await extract_program_ids_from_event(event))[:max_links]
        song_ids = (await extract_song_ids_from_event(event))[:max_links]
        album_ids = (await extract_album_ids_from_event(event))[:max_links]

        # 专辑优先：如果有专辑链接，将专辑歌曲入队
        if album_ids:
            for album_id in album_ids:
                await _enqueue_album_parse_job(bot, event, album_id, cfg)
            return

        await _enqueue_parse_jobs(bot, event, song_ids, program_ids)


# 注册到消息处理管道
register_handler(AutoNeteaseHandler())
logger.info("网易云音乐解析器已注册 → music.163.com / 163cn.tv")
