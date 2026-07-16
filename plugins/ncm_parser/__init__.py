"""
NCM 文件自动解析插件。

检测私聊或群聊中的 .ncm（网易云音乐加密）文件，
自动下载并解密，将解密后的音频文件发回聊天。
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, PrivateMessageEvent

from core.access_control import is_event_allowed
from core.activity_tracker import finish_activity, start_activity
from core.error_notifier import notify_error_to_superuser, send_user_error
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
        _ncm_sem = asyncio.Semaphore(concurrency)
    return _ncm_sem


def _has_ncm_file(event: MessageEvent) -> tuple[str, str] | None:
    """
    检查消息中是否包含 .ncm 文件。

    Returns:
        (file_id, original_filename) 如果找到，否则 None
    """
    for segment in event.get_message():
        seg_type = getattr(segment, "type", "")
        if seg_type != "file":
            continue

        data = dict(getattr(segment, "data", {}) or {})
        file_id = str(data.get("file_id", ""))
        file_name_raw = str(data.get("file", "") or data.get("name", ""))

        if not file_id:
            continue

        # 检测 .ncm 扩展名（不区分大小写）
        if file_name_raw.lower().endswith(".ncm"):
            return file_id, file_name_raw

        # 如果文件名信息中没有扩展名，但确实是 ncm 文件的 file_id
        # 某些 QQ 客户端实现可能在 file 字段中不包含扩展名，
        # 此时 file_id 本身也可能包含 .ncm，也检查一下
        if file_name_raw.lower().endswith(".ncm") or file_id.lower().endswith(".ncm"):
            return file_id, file_name_raw or file_id

    return None


@ncm_matcher.handle()
async def handle_ncm_file(bot: Bot, event: MessageEvent) -> None:
    """检测并处理消息中的 .ncm 文件。"""
    cfg = get_config()
    if not cfg.get("enabled", True):
        return
    if not cfg.get("auto_parse", True):
        return

    # 权限检查
    if not is_event_allowed(cfg, event):
        logger.debug("[NcmParser] 权限拒绝 → user=%s", event.get_user_id())
        return

    # 过滤机器人自己的消息
    if str(event.get_user_id()) == str(bot.self_id):
        return

    # 检查是否包含 .ncm 文件
    result = _has_ncm_file(event)
    if result is None:
        return

    file_id, original_filename = result
    logger.info(
        "[NcmParser] 检测到 NCM 文件 → file_id=%s, filename=%s, user=%s",
        file_id,
        original_filename,
        event.get_user_id(),
    )

    # 并发控制
    sem = _ensure_semaphore(cfg)
    async with sem:
        aid = start_activity(
            "ncm_parser",
            "decrypting",
            "解密 NCM 文件",
            description=original_filename,
        )
        try:
            # ===== 步骤 1-4: 获取文件 → 下载 → 解密 → 保存 =====
            decrypt_result = await download_and_decrypt_ncm(file_id, bot, cfg)
            if decrypt_result is None:
                logger.warning("[NcmParser] 解密结果为空 → %s", original_filename)
                return

            out_name, out_path = decrypt_result

            # ===== 步骤 5: 发送解密后的文件 =====
            await send_decrypted_file(bot, event, out_name, out_path, cfg)

            # 统计
            stats_increment(event, "ncm_parsed", 1)

            logger.info(
                "[NcmParser] 处理完成 → %s → %s, user=%s",
                original_filename,
                out_name,
                event.get_user_id(),
            )

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(
                "[NcmParser] 处理失败 → file=%s: %s",
                original_filename,
                e,
            )
            try:
                await send_user_error(bot, event)
                await notify_error_to_superuser(bot, event, e, "NcmParser")
            except Exception as notify_err:
                logger.exception("发送错误通知失败: %s", notify_err)
        finally:
            finish_activity(aid)


logger.info("NCM 文件解析器已加载 — 自动检测并解密 .ncm 文件")
