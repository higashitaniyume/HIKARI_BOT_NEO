"""
Pixiv 作品发送模块。

负责：
1. 构建作品信息文本
2. 合并转发发送（多图优先）
3. 合并转发失败后降级为逐张发送
4. 发送前先发作品信息
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent, Message, MessageSegment

from core.bot_messages import get_message as msg

from .downloader import download_with_fallback, file_as_uri
from .parser import PixivArtwork, PixivPage, fetch_artwork

logger = logging.getLogger("HikariBot.PixivSender")


def build_info_text(artwork: PixivArtwork, selected_count: int, original_count: int) -> str:
    """构建作品信息文本。"""
    r18_text = ""
    if artwork.x_restrict == 1:
        r18_text = " / R-18"
    elif artwork.x_restrict == 2:
        r18_text = " / R-18G"

    ai_text = " / AI" if artwork.ai_type == 2 else ""

    tags = " ".join(f"#{t}" for t in artwork.tags[:8])
    if tags:
        tags = "\n" + tags

    return msg(
        "pixiv.info",
        title=artwork.title,
        author=artwork.user_name,
        illust_id=artwork.illust_id,
        page_count=artwork.page_count,
        r18=r18_text,
        ai=ai_text,
        tags=tags,
    )


def select_pages(artwork: PixivArtwork, max_send: int) -> list[PixivPage]:
    """
    选择要发送的页面。

    默认发送全部页面，但不超过 max_send 限制。
    """
    if not artwork.pages:
        return []

    selected = artwork.pages[:max_send]

    if len(artwork.pages) > max_send:
        logger.info(
            f"[Pixiv] 图片数量超限裁剪 → pid={artwork.illust_id} "
            f"total={len(artwork.pages)} → selected={len(selected)}, max_send={max_send}"
        )

    return selected


async def send_artwork(
    bot: Bot,
    event: Event,
    illust_id: str,
    config: dict[str, Any],
) -> None:
    """
    发送 Pixiv 作品的完整流程：

    1. 获取作品信息
    2. 下载图片
    3. 发送作品信息文本
    4. 尝试合并转发发送图片
    5. 合并转发失败则降级为逐张发送
    """
    t_start = time.time()
    logger.info(f"[Pixiv] 开始处理作品请求 → pid={illust_id}")

    cookie = config.get("cookie", "")
    proxy = config.get("proxy", "")
    cache_dir = config.get("cache_dir", "/tmp/hikari_bot")
    max_file_mb = config.get("max_file_mb", 25)
    max_send = config.get("max_send", 6)
    allow_r18 = config.get("allow_r18", False)
    send_link_info = bool(config.get("send_link_info", True))
    send_strategy = config.get("send_strategy", {})

    # —— 获取作品信息 ——
    artwork = await fetch_artwork(illust_id, cookie, proxy)

    # —— R-18 检查 ——
    if artwork.is_r18 and not allow_r18:
        logger.info(f"[Pixiv] R-18 作品被拦截 → pid={illust_id} x_restrict={artwork.x_restrict}")
        await bot.send(
            event,
            Message(msg("pixiv.r18_blocked", illust_id=illust_id)),
        )
        return

    if artwork.is_r18:
        logger.info(f"[Pixiv] R-18 作品允许发送 → pid={illust_id}")

    # —— 选择页面 ——
    selected_pages = select_pages(artwork, max_send)

    if not selected_pages:
        await bot.send(event, Message(msg("pixiv.no_images", illust_id=illust_id)))
        return

    logger.info(
        f"[Pixiv] 页面选择 → pid={illust_id} "
        f"total={artwork.page_count}, selected={len(selected_pages)}"
    )

    # —— 下载图片 ——
    image_paths: list[Path] = []
    original_count = 0
    download_errors = 0

    for page in selected_pages:
        try:
            path, is_original = await download_with_fallback(
                page, illust_id, cookie, proxy, cache_dir, max_file_mb
            )
            image_paths.append(path)
            if is_original:
                original_count += 1
            await asyncio.sleep(0.2)  # 避免下载过快
        except Exception as e:
            download_errors += 1
            logger.exception(f"[Pixiv] 图片下载失败 → pid={illust_id} p={page.index}: {e}")

    if not image_paths:
        logger.error(f"[Pixiv] 所有图片下载失败 → pid={illust_id}")
        await bot.send(event, Message(msg("pixiv.download_failed", illust_id=illust_id)))
        return

    # —— 构建作品信息 ——
    info_text = build_info_text(artwork, len(image_paths), original_count) if send_link_info else ""

    # —— 发送图片 ——
    prefer_forward = send_strategy.get("prefer_forward_message", True)
    fallback_separate = send_strategy.get("fallback_to_separate_images", True)

    if prefer_forward and len(image_paths) > 1:
        # 多图作品：全部放合并转发（可选作品信息 + 所有图片）
        sent_forward = await _try_send_forward(bot, event, artwork, image_paths, info_text)
        if sent_forward:
            logger.info(f"[Pixiv] 合并转发发送成功 → pid={illust_id}")
        elif fallback_separate:
            # 合并转发失败，降级：先发信息，再逐张发图片
            logger.warning(f"[Pixiv] 合并转发失败，降级为逐张发送 → pid={illust_id}")
            if info_text:
                await bot.send(event, Message(info_text))
            await _send_separate_images(bot, event, image_paths, illust_id)
        else:
            logger.warning(f"[Pixiv] 合并转发失败且不降级 → pid={illust_id}")
    else:
        # 单图：先发信息，再发图片
        if info_text:
            await bot.send(event, Message(info_text))
        await _send_separate_images(bot, event, image_paths, illust_id)

    total_elapsed = time.time() - t_start
    logger.info(
        f"[Pixiv] 作品发送完成 → pid={illust_id} "
        f"发送 {len(image_paths)} 张 (原图 {original_count}), "
        f"失败 {download_errors} 张, "
        f"总耗时 {total_elapsed:.2f}s"
    )


async def _try_send_forward(
    bot: Bot,
    event: Event,
    artwork: PixivArtwork,
    image_paths: list[Path],
    info_text: str,
) -> bool:
    """
    尝试使用合并转发发送多图作品。

    Returns:
        True 如果发送成功，False 如果失败需要降级。
    """
    try:
        # 构建转发节点
        bot_self_id = int(bot.self_id)
        bot_nickname = "HikariBotNeo"

        nodes: list[MessageSegment] = []

        # 可选节点: 作品信息
        if info_text:
            nodes.append(MessageSegment.node_custom(
                user_id=bot_self_id,
                nickname=bot_nickname,
                content=Message(info_text),
            ))

        # 后续节点: 每张图片
        for i, path in enumerate(image_paths):
            uri = file_as_uri(path)
            image_msg = Message(MessageSegment.image(uri))
            nodes.append(MessageSegment.node_custom(
                user_id=bot_self_id,
                nickname=bot_nickname,
                content=image_msg,
            ))

        # 发送合并转发
        if isinstance(event, GroupMessageEvent):
            await bot.send_group_forward_msg(group_id=event.group_id, messages=nodes)
        else:
            await bot.send_private_forward_msg(user_id=int(event.get_user_id()), messages=nodes)

        logger.info(f"[Pixiv] 合并转发完成 → pid={artwork.illust_id}, 共 {len(nodes)} 个节点")
        return True

    except Exception as e:
        logger.warning(f"[Pixiv] 合并转发失败 → pid={artwork.illust_id}: {e}")
        return False


async def _send_separate_images(
    bot: Bot,
    event: Event,
    image_paths: list[Path],
    illust_id: str,
) -> None:
    """逐张发送图片（降级方案）。"""
    for i, path in enumerate(image_paths):
        uri = file_as_uri(path)
        logger.debug(f"[Pixiv] 逐张发送图片 {i+1}/{len(image_paths)} → pid={illust_id}")
        await bot.send(event, Message(MessageSegment.image(uri)))
        await asyncio.sleep(0.5)
