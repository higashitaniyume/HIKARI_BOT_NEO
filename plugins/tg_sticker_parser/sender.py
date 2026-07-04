from __future__ import annotations

import asyncio
import logging
import zipfile
from pathlib import Path

from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    MessageEvent,
    MessageSegment,
    PrivateMessageEvent,
)

from core.bot_identity import get_bot_name
from core.bot_messages import get_message as msg
from core.error_notifier import notify_error_to_superuser, send_user_error

logger = logging.getLogger("HikariBot.TgStickerSender")


async def send_gif_files(
    bot: Bot,
    event: MessageEvent,
    gif_paths: list[Path],
    max_send_count: int,
    send_delay_seconds: float,
) -> None:
    """逐个发送 GIF 文件到当前会话。"""
    send_count = min(len(gif_paths), int(max_send_count))

    for gif_path in gif_paths[:send_count]:
        uri = gif_path.resolve().as_uri()
        await bot.send(event, MessageSegment.image(uri))
        await asyncio.sleep(float(send_delay_seconds))


async def send_merged_forward_gifs(
    bot: Bot,
    event: MessageEvent,
    gif_paths: list[Path],
    title: str,
) -> None:
    """把 GIF 作为合并转发消息发送。"""
    nodes = []

    bot_uin = str(bot.self_id)
    sender_name = get_bot_name()

    for index, gif_path in enumerate(gif_paths, start=1):
        uri = gif_path.resolve().as_uri()

        nodes.append(
            {
                "type": "node",
                "data": {
                    "name": sender_name,
                    "uin": bot_uin,
                    "content": [
                        {
                            "type": "text",
                            "data": {
                                "text": f"{title} - {index}/{len(gif_paths)}\n"
                            },
                        },
                        {
                            "type": "image",
                            "data": {
                                "file": uri,
                            },
                        },
                    ],
                },
            }
        )

    if isinstance(event, GroupMessageEvent):
        await bot.call_api(
            "send_group_forward_msg",
            group_id=event.group_id,
            messages=nodes,
        )
        return

    if isinstance(event, PrivateMessageEvent):
        await bot.call_api(
            "send_private_forward_msg",
            user_id=event.user_id,
            messages=nodes,
        )
        return

    raise RuntimeError(f"不支持的事件类型，无法合并转发: {type(event).__name__}")


def make_zip_from_files(
    files: list[Path],
    zip_path: Path,
    root_name: str = "gifs",
) -> Path:
    """把所有 GIF 打成 ZIP。"""
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in files:
            if not file_path.exists() or file_path.stat().st_size <= 0:
                continue
            zf.write(file_path, arcname=f"{root_name}/{file_path.name}")

    if not zip_path.exists() or zip_path.stat().st_size <= 0:
        raise RuntimeError(f"ZIP 生成失败: {zip_path}")

    return zip_path


async def upload_zip_file(
    bot: Bot,
    event: MessageEvent,
    zip_path: Path,
    display_name: str,
) -> None:
    """把 ZIP 上传到当前会话。"""
    abs_path = str(zip_path.resolve())

    if isinstance(event, GroupMessageEvent):
        await bot.call_api(
            "upload_group_file",
            group_id=event.group_id,
            file=abs_path,
            name=display_name,
        )
        return

    if isinstance(event, PrivateMessageEvent):
        await bot.call_api(
            "upload_private_file",
            user_id=event.user_id,
            file=abs_path,
            name=display_name,
        )
        return

    raise RuntimeError(f"不支持的事件类型，无法上传 ZIP: {type(event).__name__}")


async def send_sticker_outputs(
    bot: Bot,
    event: MessageEvent,
    gif_paths: list[Path],
    output_root: Path,
    set_name: str,
    title: str,
    total_count: int = 0,
    failed_count: int = 0,
    direct_send_limit: int = 10,
    merged_send_limit: int = 80,
    send_delay_seconds: float = 0.5,
    use_zip: bool = False,
    from_cache: bool = False,
) -> None:
    """
    根据数量选择发送方式：

    1. use_zip 为 True：打包为 ZIP 发送
    2. <= direct_send_limit：逐个发 GIF
    3. 超过 limit：以合并转发形式发送（如果数量太多，则划分为多组分别发送合并转发消息）
    """
    gif_paths = [p for p in gif_paths if p.exists() and p.stat().st_size > 0]
    total = len(gif_paths)

    if total <= 0:
        await bot.send(event, msg("tg_sticker.no_gif"))
        return

    direct_send_limit = int(direct_send_limit)
    merged_send_limit = int(merged_send_limit)

    msg_lines = [msg("tg_sticker.send_title", title=title)]
    if from_cache:
        msg_lines.append(msg("tg_sticker.send_from_cache", count=total))
    elif failed_count > 0:
        msg_lines.append(msg("tg_sticker.send_partial", success_count=total, failed_count=failed_count))
    else:
        msg_lines.append(msg("tg_sticker.send_complete", count=total))

    # 1. 如果用户自选了 ZIP，则以 ZIP 发送
    if use_zip:
        zip_path = output_root / f"{set_name}_gifs.zip"
        zip_name = f"{set_name}_gifs.zip"

        make_zip_from_files(
            files=gif_paths,
            zip_path=zip_path,
            root_name=f"{set_name}_gifs",
        )

        msg_lines.append(msg("tg_sticker.send_zip"))
        await bot.send(event, "\n".join(msg_lines))
        await upload_zip_file(
            bot=bot,
            event=event,
            zip_path=zip_path,
            display_name=zip_name,
        )
        return

    # 2. 如果贴纸数量在直接发送限制内，逐个发送
    if total <= direct_send_limit:
        msg_lines.append(msg("tg_sticker.send_direct"))
        await bot.send(event, "\n".join(msg_lines))
        await send_gif_files(
            bot=bot,
            event=event,
            gif_paths=gif_paths,
            max_send_count=total,
            send_delay_seconds=send_delay_seconds,
        )
        return

    # 3. 否则以分组合并消息形式发送，不再退回至压缩包
    chunk_size = max(1, merged_send_limit)
    gif_chunks = [gif_paths[i : i + chunk_size] for i in range(0, total, chunk_size)]

    msg_lines.append(msg("tg_sticker.send_forward", count=len(gif_chunks)))
    await bot.send(event, "\n".join(msg_lines))

    try:
        for idx, chunk in enumerate(gif_chunks, start=1):
            chunk_title = f"{title} (第 {idx}/{len(gif_chunks)} 组)" if len(gif_chunks) > 1 else title
            await send_merged_forward_gifs(
                bot=bot,
                event=event,
                gif_paths=chunk,
                title=chunk_title,
            )
    except Exception as e:
        logger.exception("发送合并转发消息失败: %s", e)
        await send_user_error(bot, event)
        try:
            await notify_error_to_superuser(bot, event, e, "TgStickerForwardSend")
        except Exception as notify_error:
            logger.exception("发送管理员错误通知失败: %s", notify_error)
