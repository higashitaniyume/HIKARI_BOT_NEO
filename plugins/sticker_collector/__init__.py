from __future__ import annotations

import asyncio
import logging
import mimetypes
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, PrivateMessageEvent

from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command
from core.config_loader import load_main_config
from plugins import sticker_inbox, sticker_library
from plugins.media_transcoder import STICKER_INPUT_EXTS, TranscodeError, ensure_sticker_gif

from .config import get_config, get_target, get_targets, remove_target, set_target

logger = logging.getLogger("HikariBot.StickerCollector")

collector_matcher = on_message(priority=80, block=False)
_collect_sem = asyncio.Semaphore(1)

_PLACEHOLDER_SUPERUSER = {"", "你的QQ号"}


def _as_str_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value}


def _event_allowed(event: MessageEvent, cfg: dict[str, Any], bot: Bot, target: dict[str, Any] | None = None) -> bool:
    if not cfg.get("enabled", True):
        return False

    user_id = str(event.get_user_id())
    if user_id == str(bot.self_id):
        return False
    if user_id in _as_str_set(cfg.get("ignored_users")):
        return False

    # 定向收集是显式指定的目标，不受群聊/私聊收集开关限制。
    if target is not None:
        return True

    if isinstance(event, GroupMessageEvent):
        if not cfg.get("collect_group", True):
            return False
        allowed_groups = _as_str_set(cfg.get("allowed_groups"))
        return not allowed_groups or str(event.group_id) in allowed_groups

    if isinstance(event, PrivateMessageEvent):
        return bool(cfg.get("collect_private", True))

    return False


def _image_segments(event: MessageEvent) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for segment in event.get_message():
        if getattr(segment, "type", "") != "image":
            continue
        data = dict(getattr(segment, "data", {}) or {})
        if data.get("url"):
            segments.append(data)
    return segments


def _guess_suffix(image_data: dict[str, Any], url: str, content_type: str = "") -> str:
    candidates = [
        str(image_data.get("file") or ""),
        urlparse(url).path,
    ]
    for candidate in candidates:
        suffix = Path(candidate).suffix.lower()
        if suffix in STICKER_INPUT_EXTS:
            return suffix

    suffix = mimetypes.guess_extension(content_type.split(";", 1)[0].strip()) if content_type else ""
    if suffix == ".jpe":
        suffix = ".jpg"
    if suffix in STICKER_INPUT_EXTS:
        return suffix
    return ".jpg"


async def _download_image(url: str, dest: Path, timeout_seconds: float, max_bytes: int) -> str:
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
        tmp_path = dest.with_suffix(dest.suffix + ".part")
        tmp_path.unlink(missing_ok=True)
        try:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                content_length_bytes = int(content_length) if content_length and content_length.isdigit() else 0
                if content_length_bytes > max_bytes:
                    raise RuntimeError(
                        f"图片超过大小限制：{content_length_bytes / 1024 / 1024:.1f}MB"
                    )

                written = 0
                with tmp_path.open("wb") as f:
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                        written += len(chunk)
                        if written > max_bytes:
                            raise RuntimeError(
                                f"图片超过大小限制：{written / 1024 / 1024:.1f}MB"
                            )
                        f.write(chunk)
            tmp_path.replace(dest)
            return response.headers.get("content-type", "")
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise


def _event_metadata(event: MessageEvent, image_data: dict[str, Any]) -> dict[str, Any]:
    group_id = str(getattr(event, "group_id", "") or "")
    return {
        "source": "qq_message",
        "sender_id": str(event.get_user_id()),
        "group_id": group_id,
        "message_id": str(getattr(event, "message_id", "") or ""),
        "created_at": int(time.time()),
        "original_name": str(image_data.get("file") or "qq_image"),
    }


def _is_animated_image(path: Path) -> bool:
    """按文件头判断是否为动态图片（GIF / APNG / 动画 WebP）。

    NapCat 上报的 image 段 file 后缀不可靠（动态表情也常为 .png/.jpg），
    必须看实际内容。静态 PNG/JPEG 返回 False。
    """
    try:
        with path.open("rb") as f:
            header = f.read(64)
    except OSError:
        return False

    if header.startswith(b"GIF8"):
        # GIF（QQ 动画表情多为 GIF；单帧 GIF 罕见，一并收集）
        return True
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        # APNG 动画：PNG 头之后有 acTL chunk（IHDR 后紧跟，前 64 字节内）
        return b"acTL" in header
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        # 动画 WebP：VP8X chunk 的 animation flag（bit 1）
        # RIFF(4) + size(4) + WEBP(4) + "VP8X"(4) + chunk size(4) + flags(1)
        return header[12:16] == b"VP8X" and len(header) > 20 and bool(header[20] & 0x02)
    return False


def _target_for_event(event: MessageEvent, cfg: dict[str, Any]) -> dict[str, Any] | None:
    """命中定向收集目标时返回目标信息，否则返回 None。"""
    targets = cfg.get("target_packs") or {}
    if not isinstance(targets, dict):
        return None
    user_id = str(event.get_user_id())
    raw = targets.get(user_id)
    if not isinstance(raw, dict):
        return None
    pack = str(raw.get("pack") or "").strip()
    if not pack or not raw.get("enabled", True):
        return None
    if isinstance(event, GroupMessageEvent):
        groups = raw.get("groups") or []
        if groups and str(event.group_id) not in {str(item) for item in groups}:
            return None
    return {
        "pack": pack,
        "name": str(raw.get("name") or "").strip(),
        "user_id": user_id,
    }


async def _collect_one(bot: Bot, event: MessageEvent, image_data: dict[str, Any], target: dict[str, Any] | None) -> None:
    cfg = get_config()
    async with _collect_sem:
        temp_root = Path(str(cfg.get("temp_root", "/tmp/hikari_bot/sticker_collector")))
        temp_root.mkdir(parents=True, exist_ok=True)
        timeout_seconds = float(cfg.get("download_timeout_seconds", 30))
        max_download_mb = int(cfg.get("max_download_mb", 30))
        max_bytes = max(max_download_mb, 1) * 1024 * 1024
        max_pending = int(cfg.get("max_pending", 1000))
        url = str(image_data.get("url") or "")
        if not url:
            return

        # 定向收集只收 QQ 表情面板的动画表情：NapCat 对表情带 summary="[动画表情]" 标记，
        # 普通图片和静态表情包无标记（summary 空），下载前直接跳过。
        if target is not None and str(image_data.get("summary") or "").strip() != "[动画表情]":
            logger.info(
                "[StickerCollector] 定向收集跳过非动画表情消息: user=%s file=%s summary=%r",
                target["user_id"],
                str(image_data.get("file") or url),
                str(image_data.get("summary") or ""),
            )
            return

        raw_path: Path | None = None
        gif_path: Path | None = None
        try:
            raw_path = temp_root / f"raw_{uuid.uuid4().hex}.bin"
            content_type = await _download_image(url, raw_path, timeout_seconds, max_bytes)
            suffix = _guess_suffix(image_data, url, content_type)
            typed_path = raw_path.with_suffix(suffix)
            raw_path.replace(typed_path)
            raw_path = typed_path

            # 下载后按文件头复核（NapCat 后缀不可靠）：GIF/APNG/动画 WebP 才收。
            if target is not None and not _is_animated_image(raw_path):
                logger.info("[StickerCollector] 定向收集跳过非动态内容: user=%s file=%s", target["user_id"], str(image_data.get("file") or url))
                return

            gif_path = temp_root / f"gif_{uuid.uuid4().hex}.gif"
            await ensure_sticker_gif(raw_path, gif_path)

            if target is not None:
                # 定向收集：直接入库到目标贴纸包（按哈希自动去重），不进收件箱。
                saved = sticker_library.save_gifs_to_pack(target["pack"], [gif_path], source="qq_collect")
                if saved:
                    logger.info("[StickerCollector] 定向收集贴纸 → %s (%s)", target["pack"], saved[0].name)
            else:
                added, reason = sticker_inbox.add_gif(
                    gif_path,
                    metadata=_event_metadata(event, image_data),
                    max_pending=max_pending,
                )
                if added:
                    logger.info("[StickerCollector] 已静默收集贴纸 → %s", reason)
                else:
                    logger.debug("[StickerCollector] 跳过贴纸收集: %s", reason)
        except TranscodeError as e:
            if target is not None:
                logger.info("[StickerCollector] 定向收集转 GIF 失败: user=%s err=%s", target["user_id"], e)
            else:
                logger.debug("[StickerCollector] 图片转 GIF 失败，已跳过: %s", e)
        except Exception as e:
            if target is not None:
                logger.info("[StickerCollector] 定向收集失败: user=%s err=%s", target["user_id"], e)
            else:
                logger.debug("[StickerCollector] 静默收集图片失败，已跳过: %s", e)
        finally:
            if raw_path is not None:
                raw_path.unlink(missing_ok=True)
            if gif_path is not None:
                gif_path.unlink(missing_ok=True)


async def _collect_message(bot: Bot, event: MessageEvent, images: list[dict[str, Any]], target: dict[str, Any] | None) -> None:
    for image_data in images:
        await _collect_one(bot, event, image_data, target)


@collector_matcher.handle()
async def handle_collect_stickers(bot: Bot, event: MessageEvent) -> None:
    cfg = get_config()
    target = _target_for_event(event, cfg)
    if not _event_allowed(event, cfg, bot, target):
        return

    images = _image_segments(event)
    if not images:
        if target is not None:
            logger.info(
                "[StickerCollector] 定向目标消息无 image 段，跳过: user=%s segs=%s",
                target["user_id"],
                [getattr(segment, "type", "?") for segment in event.get_message()],
            )
        return

    # 静默后台收集，不阻塞聊天消息处理。
    asyncio.create_task(_collect_message(bot, event, images, target))


# =========================
# 定向收集命令
# =========================

def _superuser_id() -> str:
    try:
        cfg = load_main_config()
        return str(cfg.get("bot", {}).get("superuser_id") or "").strip()
    except Exception as e:
        logger.warning("[StickerCollector] 读取超级管理员配置失败: %s", e)
        return ""


async def _is_authorized(bot: Bot, event: MessageEvent) -> bool:
    """仅超级管理员或群内 owner/admin 可以使用收集命令。"""
    user_id = str(event.get_user_id())
    superuser_id = _superuser_id()
    if superuser_id and superuser_id not in _PLACEHOLDER_SUPERUSER and user_id == superuser_id:
        return True
    if isinstance(event, GroupMessageEvent):
        try:
            info = await bot.get_group_member_info(group_id=event.group_id, user_id=event.get_user_id())
            return str(info.get("role") or "") in {"owner", "admin"}
        except Exception as e:
            logger.debug("[StickerCollector] 查询群成员角色失败: %s", e)
            return False
    return False


def _extract_at_ids(event: MessageEvent) -> list[str]:
    ids: list[str] = []
    for segment in event.get_message():
        if getattr(segment, "type", "") != "at":
            continue
        data = getattr(segment, "data", {}) or {}
        qq = str(data.get("qq") or "").strip()
        if qq and qq != "all":
            ids.append(qq)
    return ids


def _resolve_user_id(ctx: CommandContext) -> str:
    """优先取 @ 的 QQ 号，否则取参数中第一个纯数字 token。"""
    at_ids = _extract_at_ids(ctx.event)
    if at_ids:
        return at_ids[0]
    parts = ctx.args.split()
    if parts and parts[0].isdigit():
        return parts[0]
    return ""


def _pack_name_from_args(ctx: CommandContext, user_id: str) -> str:
    """包名 = 参数去掉目标 QQ 号后的剩余文本。"""
    parts = ctx.args.split()
    if _extract_at_ids(ctx.event):
        return " ".join(parts).strip()
    if parts and parts[0] == user_id:
        parts = parts[1:]
    return " ".join(parts).strip()


async def _fetch_group_nickname(bot: Bot, event: MessageEvent, user_id: str) -> str:
    if isinstance(event, GroupMessageEvent):
        try:
            info = await bot.get_group_member_info(group_id=event.group_id, user_id=int(user_id))
            return str(info.get("card") or info.get("nickname") or "").strip()
        except Exception as e:
            logger.debug("[StickerCollector] 获取群成员昵称失败: %s", e)
    return ""


async def _default_pack_name(bot: Bot, event: MessageEvent, user_id: str) -> str:
    nickname = await _fetch_group_nickname(bot, event, user_id)
    return nickname or user_id


async def _deny_if_unauthorized(ctx: CommandContext) -> bool:
    if not await _is_authorized(ctx.bot, ctx.event):
        await ctx.send(Message(msg("sticker.collect_permission_denied")))
        return True
    return False


@command("收集", aliases=("开始收集",), description="收集指定群友的表情包到贴纸包", usage="收集 @某人 [包名]")
async def cmd_start_collect(ctx: CommandContext) -> None:
    if await _deny_if_unauthorized(ctx):
        return

    user_id = _resolve_user_id(ctx)
    if not user_id:
        await ctx.send(Message(msg("sticker.collect_no_target")))
        return
    if user_id == str(ctx.bot.self_id):
        await ctx.send(Message(msg("sticker.collect_bot_not_allowed")))
        return

    pack_name = _pack_name_from_args(ctx, user_id) or await _default_pack_name(ctx.bot, ctx.event, user_id)
    nickname = await _fetch_group_nickname(ctx.bot, ctx.event, user_id)
    set_target(user_id, pack=pack_name, name=nickname)
    logger.info("[StickerCollector] 开始定向收集 %s (%s) → 贴纸包 %s", nickname or user_id, user_id, pack_name)
    await ctx.send(Message(msg("sticker.collect_added", name=nickname or user_id, user_id=user_id, pack=pack_name)))


@command("停止收集", description="停止收集指定群友的表情包", usage="停止收集 @某人")
async def cmd_stop_collect(ctx: CommandContext) -> None:
    if await _deny_if_unauthorized(ctx):
        return

    user_id = _resolve_user_id(ctx)
    if not user_id:
        await ctx.send(Message(msg("sticker.collect_no_target")))
        return

    target = get_target(user_id)
    if target is None:
        await ctx.send(Message(msg("sticker.collect_remove_not_found", user_id=user_id)))
        return

    remove_target(user_id)
    logger.info("[StickerCollector] 停止定向收集 %s (%s) → 贴纸包 %s", target["name"] or user_id, user_id, target["pack"])
    await ctx.send(Message(msg("sticker.collect_removed", name=target["name"] or user_id, user_id=user_id)))


@command("收集列表", description="查看定向收集目标", usage="收集列表")
async def cmd_collect_list(ctx: CommandContext) -> None:
    if await _deny_if_unauthorized(ctx):
        return

    targets = get_targets()
    if not targets:
        await ctx.send(Message(msg("sticker.collect_list_empty")))
        return

    lines = [msg("sticker.collect_list_header")]
    for user_id, target in sorted(targets.items()):
        count = sticker_library.count_pack(target["pack"])
        groups = "、".join(target["groups"]) or msg("sticker.collect_groups_all")
        lines.append(msg(
            "sticker.collect_list_row",
            name=target["name"] or user_id,
            user_id=user_id,
            pack=target["pack"],
            count=count,
            groups=groups,
        ))
    await ctx.send(Message("\n".join(lines)))
