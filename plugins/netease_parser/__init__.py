"""
网易云音乐解析插件入口。

NoneBot 加载此插件时自动注册：
1. 自动 URL 检测 handler → 注册到 message_pipeline
2. 检测 music.163.com 歌曲链接 → API 获取 MP3 → 下载 → 发送语音
"""

import asyncio
import logging
import time

from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent

from core.access_control import is_event_allowed
from core.activity_tracker import ActivityScope
from core.bot_messages import get_message as msg
from core.error_notifier import notify_error_to_superuser, send_user_error
from core.message_pipeline import register_handler
from core.stats_tracker import increment as stats_increment

from .config import get_config
from .downloader import download_audio
from .parser import (
    extract_program_ids_from_event,
    extract_song_ids_from_event,
    fetch_program_detail,
    fetch_song_detail,
    fetch_song_url,
    has_netease_url,
)
from .sender import send_song

logger = logging.getLogger("HikariBot.NeteasePlugin")

# 触发首次加载并输出配置摘要
get_config()


def _log_card_details(event: MessageEvent) -> None:
    """将 QQ 分享卡片的详细信息输出到日志和 stdout。"""
    for segment in event.message:
        data = getattr(segment, "data", None)
        if not data:
            continue

        seg_type = getattr(segment, "type", "")
        if seg_type != "json":
            continue

        # 提取卡片 JSON
        raw_json = ""
        if isinstance(data, dict):
            raw_json = data.get("data", "") or ""
        elif isinstance(data, str):
            raw_json = data

        if not raw_json:
            continue

        try:
            import json as _json

            card = _json.loads(raw_json) if isinstance(raw_json, str) else raw_json
            meta = card.get("meta", {}) if isinstance(card, dict) else {}
            detail_1 = meta.get("detail_1", {}) if isinstance(meta, dict) else {}
            news = meta.get("news", {}) if isinstance(meta, dict) else {}

            # 提取关键字段
            app_name = _json.dumps(card.get("app", ""), ensure_ascii=False) if isinstance(card, dict) else ""
            title = detail_1.get("title", "") or news.get("title", "")
            desc = detail_1.get("desc", "") or news.get("desc", "")
            jump_url = detail_1.get("qqdocurl", "") or news.get("jumpUrl", "")
            preview = detail_1.get("preview", "") or news.get("preview", "")

            logger.info(
                "[Netease] 📦 QQ 卡片详情:\n"
                "    app=%s\n"
                "    title=%s\n"
                "    desc=%s\n"
                "    jumpUrl=%s\n"
                "    preview=%s\n"
                "    rawMeta=%s",
                app_name,
                title,
                desc,
                jump_url,
                preview,
                _json.dumps(meta, ensure_ascii=False, indent=4) if isinstance(meta, dict) else meta,
            )
            # 也输出到 stdout
            import sys as _sys

            print(
                f"[Netease] 📦 QQ 卡片详情: app={app_name} title={title} "
                f"jumpUrl={jump_url}",
                file=_sys.stderr,
            )
        except Exception as e:
            logger.debug("[Netease] 卡片 JSON 解析失败: %s", e)


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
        session_start = time.time()
        cfg = get_config()
        if not is_event_allowed(cfg, event):
            return

        # 输出 QQ 卡片详情到日志（方便调试卡片 URL 提取问题）
        _log_card_details(event)

        text = str(event.get_message())
        max_links = max(1, int(cfg.get("max_links_per_message", 5)))

        # ===== 处理播客/电台节目链接（含短链接解析 + 卡片元数据） =====
        program_ids = await extract_program_ids_from_event(event)
        program_ids_to_process = program_ids[:max_links]
        if program_ids_to_process:
            logger.info(
                "[Netease] ═══ 检测到播客节目 ═══ 共 %d 个: %s",
                len(program_ids_to_process), program_ids_to_process,
            )
            for i, pid in enumerate(program_ids_to_process):
                logger.info("[Netease] ─── 处理第 %d/%d 个播客 ───", i + 1, len(program_ids_to_process))
                try:
                    with ActivityScope(
                        "netease_parser",
                        "parsing",
                        "解析网易云播客",
                        description=f"ProgramID={pid}",
                    ):
                        await _process_single_program(bot, event, pid, cfg)
                    stats_increment(event, "netease_parsed", 1)
                    if i < len(program_ids_to_process) - 1:
                        await asyncio.sleep(1.0)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.exception("[Netease] ✗ 播客处理异常 → pid=%s", pid)
                    try:
                        await send_user_error(bot, event)
                        await notify_error_to_superuser(bot, event, e, "NeteaseParser")
                    except Exception as notify_err:
                        logger.exception("发送错误通知失败: %s", notify_err)

        # ===== 处理歌曲链接 =====
        logger.info("[Netease] 提取歌曲 ID 中（含短链接解析）...")
        ids = await extract_song_ids_from_event(event)
        if not ids:
            if not program_ids_to_process:
                logger.info("[Netease] 未提取到歌曲 ID，跳过处理")
            return

        ids_to_process = ids[:max_links]
        total_found = len(ids)

        logger.info(
            "[Netease] ═══ 自动解析触发 ═══\n"
            "    用户: %s\n"
            "    发现 %d 个链接, 处理 %d 个\n"
            "    歌曲 ID: %s\n"
            "    API 服务器: %s\n"
            "    API 超时: %ds\n"
            "    超时防御: %s",
            event.get_user_id(),
            total_found,
            len(ids_to_process),
            ids_to_process,
            cfg.get("api_base_url", "http://127.0.0.1:3000"),
            int(cfg.get("api_timeout", 30)),
            "有 (30s)" if cfg.get("api_timeout") else "无",
        )

        for i, song_id in enumerate(ids_to_process):
            logger.info(
                "[Netease] ─── 处理第 %d/%d 个歌曲 ───",
                i + 1,
                len(ids_to_process),
            )
            try:
                with ActivityScope(
                    "netease_parser",
                    "parsing",
                    "解析网易云音乐",
                    description=f"ID={song_id}",
                ):
                    await _process_single_song(bot, event, song_id, cfg)
                stats_increment(event, "netease_parsed", 1)
                if i < len(ids_to_process) - 1:
                    await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception(
                    "[Netease] ✗ 歌曲处理异常 → id=%s, 耗时=%.1fs",
                    song_id,
                    time.time() - session_start,
                )
                try:
                    await send_user_error(bot, event)
                    await notify_error_to_superuser(bot, event, e, "NeteaseParser")
                except Exception as notify_err:
                    logger.exception("发送错误通知失败: %s", notify_err)

        total_elapsed = time.time() - session_start
        logger.info(
            "[Netease] ═══ 处理完成 ═══ 共 %d 个歌曲, 总耗时 %.1fs",
            len(ids_to_process), total_elapsed,
        )


# 注册到消息处理管道
register_handler(AutoNeteaseHandler())
logger.info("网易云音乐解析器已注册 → music.163.com / 163cn.tv")
