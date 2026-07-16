"""
NCM 文件自动解析插件。

检测私聊或群聊中的 .ncm（网易云音乐加密）文件，
自动下载并解密，将解密后的音频文件发回聊天。
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, PrivateMessageEvent

from core.access_control import is_event_allowed
from core.activity_tracker import finish_activity, start_activity
from core.error_notifier import notify_error_to_superuser, send_user_error
from core.lifecycle_logging import describe_event, elapsed_ms
from core.stats_tracker import increment as stats_increment

from .config import get_config
from .sender import download_and_decrypt_ncm, send_decrypted_file

logger = logging.getLogger("HikariBot.NcmParser")

# 触发首次加载并输出配置摘要
get_config()

# =========================
# 自定义消息 Matcher
# =========================
# 在 URL 解析器之后、sticker_collector 之前检测文件消息
ncm_matcher = on_message(priority=2, block=False)

# 并发控制信号量
_ncm_sem: asyncio.Semaphore | None = None


def _ensure_semaphore(cfg: dict[str, Any]) -> asyncio.Semaphore:
    """根据配置创建或更新并发信号量。"""
    global _ncm_sem
    concurrency = max(1, int(cfg.get("concurrency", 2)))
    if _ncm_sem is None or _ncm_sem._value != concurrency:
        logger.debug(
            "[NcmParser] 初始化信号量 → concurrency=%d (was=%s)",
            concurrency,
            _ncm_sem._value if _ncm_sem else "None",
        )
        _ncm_sem = asyncio.Semaphore(concurrency)
    return _ncm_sem


def _has_ncm_file(event: MessageEvent) -> tuple[str, str] | None:
    """
    检查消息中是否包含 .ncm 文件。

    Returns:
        (file_id, original_filename) 如果找到，否则 None
    """
    msg_segments = list(event.get_message())
    logger.debug(
        "[NcmParser] 扫描消息段 → segments=%d, %s",
        len(msg_segments),
        describe_event(event),
    )

    for index, segment in enumerate(msg_segments):
        seg_type = getattr(segment, "type", "")
        logger.debug(
            "[NcmParser] 消息段 #%d → type=%s",
            index,
            seg_type,
        )

        if seg_type != "file":
            continue

        data = dict(getattr(segment, "data", {}) or {})
        file_id = str(data.get("file_id", ""))
        file_name_raw = str(data.get("file", "") or data.get("name", ""))
        file_size = str(data.get("file_size", ""))

        logger.debug(
            "[NcmParser] 检测到文件段 → file_id=%s, name=%s, size=%s",
            file_id,
            file_name_raw,
            file_size,
        )

        if not file_id:
            logger.debug("[NcmParser] 文件段缺 file_id，跳过")
            continue

        # 检测 .ncm 扩展名（不区分大小写）
        is_ncm = (
            file_name_raw.lower().endswith(".ncm")
            or file_id.lower().endswith(".ncm")
        )
        if is_ncm:
            logger.info(
                "[NcmParser] ✓ 命中 NCM 文件 → file_id=%s, raw_name=%s, size=%s",
                file_id,
                file_name_raw or "(无文件名)",
                file_size or "未知",
            )
            return file_id, file_name_raw or file_id

        logger.debug(
            "[NcmParser] 文件段非 NCM → name=%s（需以 .ncm 结尾）",
            file_name_raw or "(空)",
        )

    return None


@ncm_matcher.handle()
async def handle_ncm_file(bot: Bot, event: MessageEvent) -> None:
    """检测并处理消息中的 .ncm 文件。"""
    session_start = time.time()

    # ── 事件元数据（用于所有后续日志） ──
    sender_id = event.get_user_id()
    is_group = isinstance(event, GroupMessageEvent)
    chat_label = f"group={event.group_id}" if is_group else "private"
    msg_id = str(getattr(event, "message_id", ""))
    log_prefix = f"[NcmParser] event={msg_id} user={sender_id} {chat_label}"

    cfg = get_config()

    # 总开关检查
    if not cfg.get("enabled", True):
        logger.debug("%s → 插件未启用", log_prefix)
        return
    if not cfg.get("auto_parse", True):
        logger.debug("%s → auto_parse 未开启", log_prefix)
        return

    # 权限检查
    if not is_event_allowed(cfg, event):
        logger.info("%s → 权限拒绝", log_prefix)
        return

    # 过滤机器人自己的消息
    if sender_id == str(bot.self_id):
        logger.debug("%s → 忽略机器人自身消息", log_prefix)
        return

    # ── 步骤 0: 检测消息中的 NCM 文件 ──
    step_start = time.time()
    logger.info(
        "%s ▶ 步骤 0/5: 检测 NCM 文件 → message_segments=%s",
        log_prefix,
        [getattr(s, "type", "?") for s in event.get_message()],
    )
    result = _has_ncm_file(event)
    if result is None:
        return

    file_id, original_filename = result
    step_elapsed = time.time() - step_start
    logger.info(
        "%s ✓ 步骤 0/5 完成 (%.1fms) → file_id=%s, filename=%s",
        log_prefix,
        step_elapsed * 1000,
        file_id,
        original_filename,
    )

    # ── 并发控制 ──
    logger.debug("%s → 等待并发信号量...", log_prefix)
    sem = _ensure_semaphore(cfg)
    async with sem:
        logger.debug("%s → 已获取并发信号量, 开始处理", log_prefix)

        aid = start_activity(
            "ncm_parser",
            "decrypting",
            "解密 NCM 文件",
            description=original_filename,
        )
        try:
            # ── 步骤 1-4: 获取文件 → 下载 → 解密 → 保存 ──
            logger.info(
                "%s ▶ 步骤 1-4/5: 获取/下载/解密 NCM → file_id=%s",
                log_prefix,
                file_id,
            )
            decrypt_step_start = time.time()
            decrypt_result = await download_and_decrypt_ncm(file_id, bot, event, cfg)
            decrypt_step_elapsed = time.time() - decrypt_step_start

            if decrypt_result is None:
                logger.warning(
                    "%s ✗ 步骤 1-4/5 完成 (%.1fs) → 解密结果为空, file=%s",
                    log_prefix,
                    decrypt_step_elapsed,
                    original_filename,
                )
                return

            out_name, out_path = decrypt_result
            file_size_mb = out_path.stat().st_size / 1024 / 1024 if out_path.exists() else 0
            logger.info(
                "%s ✓ 步骤 1-4/5 完成 (%.1fs) → 输出=%s (%.1fMB)",
                log_prefix,
                decrypt_step_elapsed,
                out_name,
                file_size_mb,
            )

            # ── 步骤 5: 发送解密后的文件 ──
            step_start = time.time()
            logger.info(
                "%s ▶ 步骤 5/5: 上传解密文件 → %s (%s)",
                log_prefix,
                out_name,
                chat_label,
            )
            await send_decrypted_file(bot, event, out_name, out_path, cfg)
            step_elapsed = time.time() - step_start
            logger.info(
                "%s ✓ 步骤 5/5 完成 (%.1fs) → 上传成功",
                log_prefix,
                step_elapsed,
            )

            # 统计
            stats_increment(event, "ncm_parsed", 1)

            total_elapsed = time.time() - session_start
            logger.info(
                "%s 🎉 全部完成 (总耗时 %.1fs) → %s → %s",
                log_prefix,
                total_elapsed,
                original_filename,
                out_name,
            )

        except asyncio.CancelledError:
            raise
        except Exception as e:
            total_elapsed = time.time() - session_start
            logger.exception(
                "%s ✗ 处理失败 (%.1fs) → file=%s: %s",
                log_prefix,
                total_elapsed,
                original_filename,
                e,
            )
            try:
                await send_user_error(bot, event)
                await notify_error_to_superuser(bot, event, e, "NcmParser")
            except Exception as notify_err:
                logger.exception(
                    "%s → 发送错误通知也失败了: %s",
                    log_prefix,
                    notify_err,
                )
        finally:
            finish_activity(aid)
            logger.debug(
                "%s → 信号量释放, activity 已标记完成",
                log_prefix,
            )


logger.info("NCM 文件解析器已加载 — 自动检测并解密 .ncm 文件")
