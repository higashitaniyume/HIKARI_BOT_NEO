"""从合并转发（含嵌套合并转发）里提取表情包，转 GIF 导入贴纸库。

用法：引用（回复）一条合并转发消息，发送「导出表情 [贴纸包名]」。
机器人会递归展开合并转发里的每一层聊天记录，抠出其中的（动画）表情，
下载后统一转成 GIF 存入指定贴纸包（默认包名见配置 default_pack）。

只 SEND 合并转发是各解析插件已有的能力；这里补的是「接收并展开」方向：
调用 OneBot / NapCat 的 get_forward_msg 按 res_id 拉取合并转发内容，
嵌套的 forward 段再按 id 递归拉取。
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent

from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command, is_superuser_event
from plugins import sticker_library
from plugins.media_transcoder import STICKER_INPUT_EXTS, TranscodeError, ensure_sticker_gif

from .config import get_config

logger = logging.getLogger("HikariBot.ForwardStickerExport")

_ANIMATED_SUMMARY = "[动画表情]"


# =========================
# 合并转发展开
# =========================

def _seg_parts(seg: Any) -> tuple[str, dict[str, Any]]:
    """从 dict 段或 MessageSegment 对象里取 (type, data)。"""
    if isinstance(seg, dict):
        raw_type = seg.get("type")
        raw_data = seg.get("data")
    else:
        raw_type = getattr(seg, "type", None)
        raw_data = getattr(seg, "data", None)
    return str(raw_type or ""), raw_data if isinstance(raw_data, dict) else {}


async def _call_get_forward_msg(bot: Bot, forward_id: str, timeout: float) -> Any:
    """调用 get_forward_msg。NapCat/go-cqhttp 参数名在 message_id / id 间有差异，两者都试。"""
    last_err: Exception | None = None
    for kwargs in ({"message_id": forward_id}, {"id": forward_id}):
        try:
            return await asyncio.wait_for(
                bot.call_api("get_forward_msg", **kwargs), timeout=timeout
            )
        except asyncio.TimeoutError as e:
            last_err = e
            logger.warning("[ForwardExport] get_forward_msg 超时 id=%s kwargs=%s", forward_id, list(kwargs))
        except Exception as e:  # noqa: BLE001 - 尝试下一种参数名
            last_err = e
            logger.debug("[ForwardExport] get_forward_msg 失败 kwargs=%s: %s", list(kwargs), e)
    if last_err is not None:
        logger.warning("[ForwardExport] get_forward_msg 最终失败 id=%s: %s", forward_id, last_err)
    return None


async def _collect_images(
    bot: Bot,
    forward_id: str,
    *,
    depth: int,
    cfg: dict[str, Any],
    seen_ids: set[str],
    images: list[dict[str, Any]],
) -> None:
    """按 res_id 拉取合并转发，递归展开嵌套转发，把所有 image 段的 data 收进 images。"""
    max_depth = int(cfg.get("max_depth", 6))
    max_images = int(cfg.get("max_images", 300))
    timeout = float(cfg.get("forward_api_timeout_seconds", 30))

    if depth > max_depth or len(images) >= max_images:
        return
    if not forward_id or forward_id in seen_ids:
        return
    seen_ids.add(forward_id)

    resp = await _call_get_forward_msg(bot, forward_id, timeout)
    data = resp.get("data") if isinstance(resp, dict) else resp
    if not isinstance(data, dict):
        # 有的实现直接返回 {"messages": [...]}，没有 data 包裹
        data = resp if isinstance(resp, dict) else {}
    messages = data.get("messages")
    if not isinstance(messages, list):
        return

    for node in messages:
        await _walk_node_segments(bot, node, depth=depth, cfg=cfg, seen_ids=seen_ids, images=images)
        if len(images) >= max_images:
            return


def _node_segments(node: Any) -> list[Any]:
    """从一条转发节点里取出消息段列表（不同实现用 message / content / data.content）。"""
    if isinstance(node, dict):
        for key in ("message", "content"):
            value = node.get(key)
            if isinstance(value, list):
                return value
        inner = node.get("data")
        if isinstance(inner, dict) and isinstance(inner.get("content"), list):
            return inner["content"]
    return []


async def _walk_node_segments(
    bot: Bot,
    node: Any,
    *,
    depth: int,
    cfg: dict[str, Any],
    seen_ids: set[str],
    images: list[dict[str, Any]],
) -> None:
    max_images = int(cfg.get("max_images", 300))
    for seg in _node_segments(node):
        if len(images) >= max_images:
            return
        seg_type, seg_data = _seg_parts(seg)
        if seg_type == "image":
            if seg_data.get("url") or seg_data.get("file"):
                images.append(seg_data)
        elif seg_type == "forward":
            # 嵌套合并转发：优先用内联 content（避免多一次 API 调用），否则按 id 递归拉取。
            inline = seg_data.get("content")
            if isinstance(inline, list):
                for inner_node in inline:
                    await _walk_node_segments(
                        bot, inner_node, depth=depth + 1, cfg=cfg, seen_ids=seen_ids, images=images
                    )
            inner_id = str(seg_data.get("id") or "")
            if inner_id:
                await _collect_images(
                    bot, inner_id, depth=depth + 1, cfg=cfg, seen_ids=seen_ids, images=images
                )


# =========================
# 定位被引用的合并转发
# =========================

def _find_forward_id_in_segments(segments: Any) -> str:
    if not isinstance(segments, (list, tuple)):
        return ""
    for seg in segments:
        seg_type, seg_data = _seg_parts(seg)
        if seg_type == "forward":
            fid = str(seg_data.get("id") or "")
            if fid:
                return fid
    return ""


async def _resolve_forward_id(bot: Bot, event: MessageEvent) -> str:
    """从被引用消息里找出合并转发的 res_id。"""
    reply = getattr(event, "reply", None)
    if reply is not None:
        fid = _find_forward_id_in_segments(getattr(reply, "message", None))
        if fid:
            return fid
        reply_mid = getattr(reply, "message_id", None)
        if reply_mid is not None:
            fid = await _forward_id_from_get_msg(bot, reply_mid)
            if fid:
                return fid
    # 兜底：命令消息本身若直接携带 forward 段也认。
    return _find_forward_id_in_segments(event.get_message())


async def _forward_id_from_get_msg(bot: Bot, message_id: Any) -> str:
    try:
        resp = await bot.call_api("get_msg", message_id=int(message_id))
    except Exception as e:  # noqa: BLE001
        logger.debug("[ForwardExport] get_msg 回查失败: %s", e)
        return ""
    if isinstance(resp, dict):
        data = resp.get("data") if isinstance(resp.get("data"), dict) else resp
        return _find_forward_id_in_segments(data.get("message"))
    return ""


# =========================
# 下载 + 转码 + 入库
# =========================

def _guess_suffix(image_data: dict[str, Any], url: str, content_type: str = "") -> str:
    for candidate in (str(image_data.get("file") or ""), urlparse(url).path):
        suffix = Path(candidate).suffix.lower()
        if suffix in STICKER_INPUT_EXTS:
            return suffix
    suffix = mimetypes.guess_extension(content_type.split(";", 1)[0].strip()) if content_type else ""
    if suffix == ".jpe":
        suffix = ".jpg"
    if suffix in STICKER_INPUT_EXTS:
        return suffix
    return ".jpg"


async def _download(url: str, dest: Path, timeout_seconds: float, max_bytes: int) -> str:
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
        tmp_path = dest.with_suffix(dest.suffix + ".part")
        tmp_path.unlink(missing_ok=True)
        try:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length and content_length.isdigit() and int(content_length) > max_bytes:
                    raise RuntimeError(f"图片超过大小限制：{int(content_length) / 1024 / 1024:.1f}MB")
                written = 0
                with tmp_path.open("wb") as f:
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                        written += len(chunk)
                        if written > max_bytes:
                            raise RuntimeError(f"图片超过大小限制：{written / 1024 / 1024:.1f}MB")
                        f.write(chunk)
            tmp_path.replace(dest)
            return response.headers.get("content-type", "")
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise


async def _export_one(
    image_data: dict[str, Any],
    *,
    pack: str,
    cfg: dict[str, Any],
    sem: asyncio.Semaphore,
) -> bool:
    """下载单张图片 → 转 GIF → 入库。返回是否成功保存。"""
    async with sem:
        temp_root = Path(str(cfg.get("temp_root", "/tmp/hikari_bot/forward_sticker_export")))
        temp_root.mkdir(parents=True, exist_ok=True)
        timeout_seconds = float(cfg.get("download_timeout_seconds", 30))
        max_bytes = max(int(cfg.get("max_download_mb", 30)), 1) * 1024 * 1024
        url = str(image_data.get("url") or image_data.get("file") or "")
        if not url or not url.lower().startswith(("http://", "https://")):
            return False

        raw_path: Path | None = None
        gif_path: Path | None = None
        try:
            raw_path = temp_root / f"raw_{uuid.uuid4().hex}.bin"
            content_type = await _download(url, raw_path, timeout_seconds, max_bytes)
            suffix = _guess_suffix(image_data, url, content_type)
            typed_path = raw_path.with_suffix(suffix)
            raw_path.replace(typed_path)
            raw_path = typed_path

            gif_path = temp_root / f"gif_{uuid.uuid4().hex}.gif"
            await ensure_sticker_gif(raw_path, gif_path)

            saved = sticker_library.save_gifs_to_pack(pack, [gif_path], source="forward_export")
            return bool(saved)
        except TranscodeError as e:
            logger.info("[ForwardExport] 转 GIF 失败，跳过: %s", e)
            return False
        except Exception as e:  # noqa: BLE001
            logger.info("[ForwardExport] 导出单张失败，跳过: %s", e)
            return False
        finally:
            if raw_path is not None:
                raw_path.unlink(missing_ok=True)
            if gif_path is not None:
                gif_path.unlink(missing_ok=True)


# =========================
# 权限
# =========================

async def _is_authorized(bot: Bot, event: MessageEvent) -> bool:
    if is_superuser_event(event):
        return True
    if isinstance(event, GroupMessageEvent):
        try:
            info = await bot.get_group_member_info(group_id=event.group_id, user_id=event.get_user_id())
            return str(info.get("role") or "") in {"owner", "admin"}
        except Exception as e:  # noqa: BLE001
            logger.debug("[ForwardExport] 查询群成员角色失败: %s", e)
            return False
    return False


# =========================
# 命令
# =========================

@command(
    "导出表情",
    aliases=("导出表情包", "提取表情", "提取表情包"),
    description="引用合并转发消息，把里面（含嵌套记录）的表情包导入贴纸包",
    usage="引用合并转发消息 → 导出表情 [贴纸包名]",
    category="贴纸",
)
async def cmd_export_forward_stickers(ctx: CommandContext) -> None:
    cfg = get_config()
    if not cfg.get("enabled", True):
        return

    if cfg.get("require_admin", True) and not await _is_authorized(ctx.bot, ctx.event):
        await ctx.send(Message(msg("forward_export.permission_denied")))
        return

    forward_id = await _resolve_forward_id(ctx.bot, ctx.event)
    if not forward_id:
        await ctx.send(Message(msg("forward_export.no_forward")))
        return

    pack = ctx.args.strip() or str(cfg.get("default_pack", "合并转发导出"))

    await ctx.send(Message(msg("forward_export.scanning")))

    images: list[dict[str, Any]] = []
    try:
        await _collect_images(
            ctx.bot, forward_id, depth=0, cfg=cfg, seen_ids=set(), images=images
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("[ForwardExport] 展开合并转发失败: %s", e)
        await ctx.send(Message(msg("forward_export.expand_failed")))
        return

    if cfg.get("animated_only", False):
        candidates = [
            img for img in images
            if str(img.get("summary") or "").strip() == _ANIMATED_SUMMARY
        ]
    else:
        candidates = images

    logger.info(
        "[ForwardExport] 展开完成 id=%s 图片总数=%d 待导出=%d animated_only=%s pack=%r",
        forward_id, len(images), len(candidates), cfg.get("animated_only", False), pack,
    )

    if not candidates:
        await ctx.send(Message(msg("forward_export.nothing", total=len(images))))
        return

    sem = asyncio.Semaphore(max(int(cfg.get("concurrency", 3)), 1))
    results = await asyncio.gather(
        *(_export_one(img, pack=pack, cfg=cfg, sem=sem) for img in candidates)
    )
    saved = sum(1 for ok in results if ok)
    failed = len(candidates) - saved

    logger.info("[ForwardExport] 导出完成 pack=%r 成功=%d 失败=%d", pack, saved, failed)

    if saved <= 0:
        await ctx.send(Message(msg("forward_export.all_failed", count=len(candidates))))
        return
    await ctx.send(Message(msg("forward_export.done", saved=saved, failed=failed, pack=pack)))
