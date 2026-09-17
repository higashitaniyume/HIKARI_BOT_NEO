"""
QQ 音乐解析插件的处理流程。

单首歌的完整链路：

    链接/卡片 → 接口解析详情（songid 顺带换算成 songmid）→ yt-dlp 下载 → 发送

失败时把异常类型映射成不同的用户提示。这里最关键的一步是
:func:`explain_no_format`：yt-dlp 在「一个格式都没有」时一律报
``only available for registered users``，而这个提示在 ID 类型错误或纯 VIP 场景下
都是误导，必须结合接口的 ``pay_play`` 与是否配置 cookie 重新归因。
"""

from __future__ import annotations

import logging
from typing import Any

from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent

from core.bot_messages import get_message as msg

from .api import QQTrackDetail, fetch_track_detail
from .config import cookiefile_display_path, get_cookiefile
from .downloader import QQAudioResult, download_qqmusic_audio
from .errors import (
    QQMusicCookieRequiredError,
    QQMusicError,
    QQMusicNoFormatError,
    QQMusicResolveError,
    QQMusicSizeError,
    QQMusicVipRequiredError,
)
from .parser import QQSongRef
from .sender import build_info_text, send_qqmusic_audio

logger = logging.getLogger("HikariBot.QQMusicPlugin")


def has_cookie(cfg: dict[str, Any]) -> bool:
    """当前配置的 cookie 文件是否存在。"""
    path = get_cookiefile(cfg)
    return path is not None and path.is_file()


def user_message_for_error(exc: Exception, cfg: dict[str, Any]) -> str:
    """把异常映射成用户可见提示。

    更具体的类型必须先判断——它们都是 :class:`QQMusicError` 的子类。
    """
    if isinstance(exc, QQMusicResolveError):
        return msg("qqmusic.resolve_failed")
    if isinstance(exc, QQMusicVipRequiredError):
        return msg("qqmusic.vip_required")
    if isinstance(exc, QQMusicCookieRequiredError):
        return msg("qqmusic.cookie_required", path=cookiefile_display_path(cfg))
    if isinstance(exc, QQMusicSizeError):
        return msg("qqmusic.size_exceeded")
    if isinstance(exc, QQMusicError):
        return msg("qqmusic.failed", reason=str(exc))
    return msg("error.user")


def explain_no_format(detail: QQTrackDetail, cfg: dict[str, Any]) -> QQMusicError:
    """把「没有可用格式」拆成具体原因。"""
    if detail.vip_only:
        logger.info(
            "[QQMusic] 归因：VIP/付费曲目 → %s (%s, pay_play=1)",
            detail.songmid, detail.name or "未知",
        )
        return QQMusicVipRequiredError("VIP/付费曲目")

    if not has_cookie(cfg):
        logger.info(
            "[QQMusic] 归因：缺少登录 cookie → %s (%s)",
            detail.songmid, detail.name or "未知",
        )
        return QQMusicCookieRequiredError("缺少 cookie")

    logger.warning(
        "[QQMusic] 归因：已有 cookie 但仍无可用格式 → %s (%s)",
        detail.songmid, detail.name or "未知",
    )
    return QQMusicNoFormatError("该曲目当前不可下载。")


async def download_with_reason(detail: QQTrackDetail, cfg: dict[str, Any]) -> QQAudioResult:
    """下载音频，并把「无可用格式」翻译成具体原因。"""
    try:
        return await download_qqmusic_audio(detail.songmid, cfg)
    except QQMusicNoFormatError as exc:
        raise explain_no_format(detail, cfg) from exc


async def process_song(
    bot: Bot,
    event: MessageEvent,
    ref: QQSongRef,
    cfg: dict[str, Any],
) -> QQAudioResult:
    """处理一首歌：解析详情 → 下载 → 发送。

    Raises:
        QQMusicError: 任一阶段失败（见 :func:`user_message_for_error`）。
    """
    detail = await fetch_track_detail(ref, cfg)
    result = await download_with_reason(detail, cfg)

    if bool(cfg.get("send_link_info", True)):
        logger.info(
            "[QQMusic] 发送歌曲信息 → %s | %s | %s",
            detail.name, detail.singer_text, result.quality_label,
        )
        await bot.send(event, Message(build_info_text(detail, result)))

    await send_qqmusic_audio(bot, event, detail, result, cfg)
    return result
