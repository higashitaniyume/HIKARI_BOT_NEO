"""
Cobalt 媒体发送模块。

负责：
1. 构建来源信息文本
2. 多图/多视频使用合并转发发送
3. 合并转发失败后降级为逐条发送
4. 发送前先发来源信息
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent, Message, MessageSegment

from core.bot_messages import get_message as msg

from .downloader import download_media, file_as_uri
from .parser import CobaltResult, call_cobalt_api

logger = logging.getLogger("HikariBot.CobaltSender")


def build_info_text(result: CobaltResult) -> str:
    """构建来源信息文本。"""
    service_name = result.service or "社交媒体"
    service_name = service_name.capitalize()

    audio = msg("cobalt.audio_suffix") if result.audio_url else ""
    if result.items:
        media_types = set(item.media_type for item in result.items)
        types_str = "/".join(sorted(media_types))
    short_url = result.source_url[:100]
    if len(result.source_url) > 100:
        short_url += "..."
    if result.items:
        return msg(
            "cobalt.info",
            service=service_name,
            count=len(result.items),
            types=types_str,
            audio=audio,
            url=short_url,
        )
    return msg("cobalt.info_without_items", service=service_name, audio=audio, url=short_url)


async def send_cobalt_result(
    bot: Bot,
    event: Event,
    result: CobaltResult,
    config: dict[str, Any],
) -> None:
    """
    发送 Cobalt 解析结果的完整流程：

    1. 下载所有媒体文件
    2. 发送来源信息
    3. 尝试合并转发发送
    4. 合并转发失败则降级为逐条发送
    """
    t_start = time.time()

    cache_dir = config.get("cache_dir", "/tmp/hikari_bot")
    api_timeout = config.get("api_timeout", 90)
    max_file_mb = config.get("max_file_mb", 200)
    max_send = config.get("max_send", 6)
    send_link_info = bool(config.get("send_link_info", True))
    send_strategy = config.get("send_strategy", {})

    items = result.items[:max_send]

    # —— 下载媒体 ——
    media_paths: list[Path] = []
    media_types: list[str] = []
    download_errors = 0

    for item in items:
        try:
            path = await download_media(item.url, "", cache_dir, api_timeout, max_file_mb)
            media_paths.append(path)
            media_types.append(item.media_type)
            await asyncio.sleep(0.3)
        except Exception as e:
            download_errors += 1
            logger.exception(f"[Cobalt] 下载失败 → {item.url[:60]}...: {e}")

    if not media_paths:
        logger.error(f"[Cobalt] 所有媒体下载失败")
        await bot.send(event, Message(msg("cobalt.download_failed")))
        return

    # —— 构建来源信息 ——
    info_text = build_info_text(result) if send_link_info else ""

    # —— 发送媒体 ——
    prefer_forward = send_strategy.get("prefer_forward_message", True)
    fallback_separate = send_strategy.get("fallback_to_separate_media", True)

    if prefer_forward and len(media_paths) > 1:
        # 多个媒体：全部放合并转发（可选来源信息 + 所有媒体）
        sent_forward = await _try_send_forward(bot, event, media_paths, media_types, info_text)
        if sent_forward:
            logger.info(f"[Cobalt] 合并转发发送成功")
        elif fallback_separate:
            # 合并转发失败，降级：先发信息，再逐条发媒体
            logger.warning(f"[Cobalt] 合并转发失败，降级为逐条发送")
            if info_text:
                await bot.send(event, Message(info_text))
            await _send_separate(bot, event, media_paths, media_types)
        else:
            logger.warning(f"[Cobalt] 合并转发失败且不降级")
    else:
        # 单个媒体：先发信息，再发媒体
        if info_text:
            await bot.send(event, Message(info_text))
        await _send_separate(bot, event, media_paths, media_types)

    total_elapsed = time.time() - t_start
    logger.info(
        f"[Cobalt] 发送完成 → "
        f"发送 {len(media_paths)} 个媒体, 失败 {download_errors} 个, "
        f"总耗时 {total_elapsed:.2f}s"
    )


async def _try_send_forward(
    bot: Bot,
    event: Event,
    media_paths: list[Path],
    media_types: list[str],
    info_text: str,
) -> bool:
    """尝试使用合并转发发送多图/多视频。"""
    try:
        bot_self_id = int(bot.self_id)
        bot_nickname = "HIKARI"

        nodes: list[MessageSegment] = []

        # 可选节点: 来源信息
        if info_text:
            nodes.append(MessageSegment.node_custom(
                user_id=bot_self_id,
                nickname=bot_nickname,
                content=Message(info_text),
            ))

        # 后续节点: 每个媒体
        for i, path in enumerate(media_paths):
            uri = file_as_uri(path)
            mt = media_types[i] if i < len(media_types) else "photo"

            if mt == "video":
                media_msg = Message(MessageSegment.video(uri))
            else:
                media_msg = Message(MessageSegment.image(uri))

            nodes.append(MessageSegment.node_custom(
                user_id=bot_self_id,
                nickname=bot_nickname,
                content=media_msg,
            ))

        if isinstance(event, GroupMessageEvent):
            await bot.send_group_forward_msg(group_id=event.group_id, messages=nodes)
        else:
            await bot.send_private_forward_msg(user_id=int(event.get_user_id()), messages=nodes)

        logger.info(f"[Cobalt] 合并转发完成 → 共 {len(nodes)} 个节点")
        return True

    except Exception as e:
        logger.warning(f"[Cobalt] 合并转发失败: {e}")
        return False


async def _send_separate(
    bot: Bot,
    event: Event,
    media_paths: list[Path],
    media_types: list[str],
) -> None:
    """逐条发送媒体（降级方案）。"""
    for i, path in enumerate(media_paths):
        uri = file_as_uri(path)
        mt = media_types[i] if i < len(media_types) else "photo"

        if mt == "video":
            media_msg = Message(MessageSegment.video(uri))
        else:
            media_msg = Message(MessageSegment.image(uri))

        logger.debug(f"[Cobalt] 逐条发送 {i+1}/{len(media_paths)} ({mt})")
        await bot.send(event, media_msg)
        await asyncio.sleep(0.5)
