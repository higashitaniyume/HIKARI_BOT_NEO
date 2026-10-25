"""QQ 空间插件：用 NapCat 当前登录的 QQ 发说说、看动态、点赞和评论。"""

from __future__ import annotations

import logging
import time
from typing import Any

from core.access_control import is_event_allowed
from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command

from . import ai_tools, service  # noqa: F401  (ai_tools 负责注册 AI 工具)
from .config import VISIBLE_LABELS, VISIBLE_VALUES, get_config
from .service import FeedItem

logger = logging.getLogger("HikariBot.QZone")

# 「空间动态」结果缓存：会话 -> (写入时间, 动态列表)，供后续按序号点赞/评论
_FEED_TTL_SECONDS = 600.0
_FEED_CACHE_LIMIT = 200
_feed_cache: dict[str, tuple[float, list[FeedItem]]] = {}

_VISIBLE_KEYWORDS: dict[str, int] = {
    "公开": 1,
    "好友": 3,
    "好友可见": 3,
    "私密": 4,
    "仅自己": 4,
}


@command(
    "发说说",
    aliases=("发空间", "空间说说"),
    description="发布一条 QQ 空间说说",
    usage="发说说 [公开|好友|私密] <内容>",
    detail_key="qzone.help",
    category="空间",
    superuser_only=True,
)
async def handle_publish(ctx: CommandContext) -> None:
    cfg = get_config()
    if not cfg["enabled"] or not is_event_allowed(cfg, ctx.event):
        return

    visible, content = parse_publish_args(ctx.args, default_visible=cfg["default_visible"])
    if not content:
        await ctx.send(msg("qzone.publish_usage"))
        return
    if len(content) > cfg["max_length"]:
        await ctx.send(msg("qzone.text_too_long", limit=cfg["max_length"]))
        return

    try:
        result = await service.publish(ctx.bot, content, visible=visible, timeout=cfg["timeout"])
    except (service.QzoneLoginError, service.QzoneActionError) as exc:
        await _reply_failure(ctx, exc, action="publish")
        return

    logger.info(
        "[QZone] 已发布说说 uin=%s tid=%s visible=%s len=%d",
        ctx.bot.self_id,
        result.get("tid") or "-",
        visible,
        len(content),
    )
    await ctx.send(
        msg(
            "qzone.publish_ok",
            tid=result.get("tid") or "-",
            visible=VISIBLE_LABELS.get(visible, "公开"),
        )
    )


@command(
    "删说说",
    aliases=("删除说说",),
    description="删除一条自己发布的说说",
    usage="删说说 <序号|tid>",
    category="空间",
    superuser_only=True,
)
async def handle_delete(ctx: CommandContext) -> None:
    cfg = get_config()
    if not cfg["enabled"] or not is_event_allowed(cfg, ctx.event):
        return

    target = ctx.args.strip()
    if not target:
        await ctx.send(msg("qzone.delete_usage"))
        return

    if target.isdigit() and len(target) <= 2:
        cached = _get_cached_feed(ctx)
        if cached is None:
            await ctx.send(msg("qzone.feed_cache_miss"))
            return
        item = _pick_cached_item(target, cached)
        if item is None:
            await ctx.send(msg("qzone.index_invalid"))
            return
        tid, curkey = item.tid, item.curkey
    else:
        tid, curkey = target, ""

    try:
        await service.delete(ctx.bot, tid, curkey=curkey, timeout=cfg["timeout"])
    except (service.QzoneLoginError, service.QzoneActionError) as exc:
        await _reply_failure(ctx, exc, action="delete")
        return

    logger.info("[QZone] 已删除说说 uin=%s tid=%s", ctx.bot.self_id, tid)
    await ctx.send(msg("qzone.delete_ok", tid=tid))


@command(
    "空间动态",
    aliases=("空间feed", "空间列表"),
    description="查看好友的 QQ 空间动态",
    usage="空间动态 [数量]",
    category="空间",
)
async def handle_feed(ctx: CommandContext) -> None:
    cfg = get_config()
    if not cfg["enabled"] or not is_event_allowed(cfg, ctx.event):
        return

    count = _parse_count(ctx.args, default=cfg["feed_count"], maximum=20)
    if count is None:
        await ctx.send(msg("qzone.feed_usage"))
        return

    try:
        items = await service.fetch_feed(ctx.bot, count, timeout=cfg["timeout"])
    except (service.QzoneLoginError, service.QzoneActionError) as exc:
        await _reply_failure(ctx, exc, action="feed")
        return

    if not items:
        await ctx.send(msg("qzone.feed_empty"))
        return

    _store_feed(ctx, items)
    lines = [msg("qzone.feed_header", count=len(items))]
    for index, item in enumerate(items, 1):
        lines.append(msg("qzone.feed_row", index=index, nickname=item.nickname, content=_feed_text(item)))
        meta = msg(
            "qzone.feed_row_meta",
            time=_format_abstime(item.abstime),
            tid=item.tid,
        ).rstrip()
        if meta:
            lines.append(meta)
    lines.append(msg("qzone.feed_footer"))
    await ctx.send("\n".join(lines))
    logger.info("[QZone] 已拉取好友动态 uin=%s count=%d", ctx.bot.self_id, len(items))


@command(
    "好友说说",
    aliases=("空间说说列表",),
    description="查看指定好友（或自己）的说说",
    usage="好友说说 <QQ号> [数量]",
    category="空间",
)
async def handle_moods(ctx: CommandContext) -> None:
    cfg = get_config()
    if not cfg["enabled"] or not is_event_allowed(cfg, ctx.event):
        return

    parts = ctx.args.split()
    if not parts or not parts[0].isdigit():
        await ctx.send(msg("qzone.moods_usage"))
        return
    target_qq = parts[0]
    count = _parse_count(" ".join(parts[1:]), default=cfg["moods_count"], maximum=20)
    if count is None:
        await ctx.send(msg("qzone.moods_usage"))
        return

    try:
        items = await service.fetch_moods(ctx.bot, target_qq, count, timeout=cfg["timeout"])
    except (service.QzoneLoginError, service.QzoneActionError) as exc:
        await _reply_failure(ctx, exc, action="moods")
        return

    if not items:
        await ctx.send(msg("qzone.moods_empty", qq=target_qq))
        return

    lines = [msg("qzone.moods_header", qq=target_qq, count=len(items))]
    for index, item in enumerate(items, 1):
        lines.append(msg("qzone.moods_row", index=index, content=_truncate(item.content, 100)))
        lines.append(
            msg(
                "qzone.moods_row_meta",
                time=item.created_at or "-",
                tid=item.tid,
                comments=item.comment_count,
            ).rstrip()
        )
    await ctx.send("\n".join(lines))


@command(
    "说说赞",
    aliases=("点赞说说",),
    description="给「空间动态」里的某条说说点赞",
    usage="说说赞 <序号>",
    category="空间",
    superuser_only=True,
)
async def handle_like(ctx: CommandContext) -> None:
    cfg = get_config()
    if not cfg["enabled"] or not is_event_allowed(cfg, ctx.event):
        return

    cached = _get_cached_feed(ctx)
    item = _pick_cached_item(ctx.args.strip(), cached)
    if item is None:
        await ctx.send(msg("qzone.index_invalid") if cached is not None else msg("qzone.feed_cache_miss"))
        return

    try:
        await service.like(ctx.bot, item, timeout=cfg["timeout"])
    except (service.QzoneLoginError, service.QzoneActionError) as exc:
        await _reply_failure(ctx, exc, action="like")
        return

    logger.info("[QZone] 已点赞 uin=%s tid=%s", ctx.bot.self_id, item.tid)
    await ctx.send(msg("qzone.like_ok", nickname=item.nickname))


@command(
    "说说评论",
    aliases=("评论说说",),
    description="评论「空间动态」里的某条说说",
    usage="说说评论 <序号> <内容>",
    category="空间",
    superuser_only=True,
)
async def handle_comment(ctx: CommandContext) -> None:
    cfg = get_config()
    if not cfg["enabled"] or not is_event_allowed(cfg, ctx.event):
        return

    index_text, _, content = ctx.args.strip().partition(" ")
    content = content.strip()
    cached = _get_cached_feed(ctx)
    item = _pick_cached_item(index_text.strip(), cached)
    if item is None:
        await ctx.send(msg("qzone.index_invalid") if cached is not None else msg("qzone.feed_cache_miss"))
        return
    if not content:
        await ctx.send(msg("qzone.comment_usage"))
        return
    if len(content) > cfg["max_length"]:
        await ctx.send(msg("qzone.text_too_long", limit=cfg["max_length"]))
        return

    try:
        await service.comment(ctx.bot, item, content, timeout=cfg["timeout"])
    except (service.QzoneLoginError, service.QzoneActionError) as exc:
        await _reply_failure(ctx, exc, action="comment")
        return

    logger.info("[QZone] 已评论 uin=%s tid=%s len=%d", ctx.bot.self_id, item.tid, len(content))
    await ctx.send(msg("qzone.comment_ok", nickname=item.nickname))


# ── 参数与缓存 ─────────────────────────────────────────────


def parse_publish_args(args: str, *, default_visible: str = "public") -> tuple[int, str]:
    """解析「[公开|好友|私密] 内容」，返回 (可见范围, 内容)。"""
    default = VISIBLE_VALUES.get(default_visible, 1)
    text = str(args or "").strip()
    if not text:
        return default, ""

    head, _, rest = text.partition(" ")
    visible = _VISIBLE_KEYWORDS.get(head)
    if visible is not None:
        return visible, rest.strip()
    return default, text


def _parse_count(args: str, *, default: int, maximum: int) -> int | None:
    text = str(args or "").strip()
    if not text:
        return default
    if not text.isdigit():
        return None
    return min(max(int(text), 1), maximum)


def _feed_text(item: FeedItem) -> str:
    content = _truncate(item.content, 80)
    if item.image_count:
        return f"{content}（图x{item.image_count}）" if content else f"[图片 x{item.image_count}]"
    return content or "[无正文]"


def _cache_key(ctx: CommandContext) -> str:
    try:
        return f"{ctx.event.get_session_id()}:{ctx.event.get_user_id()}"
    except Exception:  # pragma: no cover - 事件字段异常时退回全局键
        return "default"


def _store_feed(ctx: CommandContext, items: list[FeedItem]) -> None:
    if len(_feed_cache) >= _FEED_CACHE_LIMIT:
        oldest = min(_feed_cache, key=lambda key: _feed_cache[key][0])
        _feed_cache.pop(oldest, None)
    _feed_cache[_cache_key(ctx)] = (time.monotonic(), list(items))


def _get_cached_feed(ctx: CommandContext) -> list[FeedItem] | None:
    entry = _feed_cache.get(_cache_key(ctx))
    if entry is None:
        return None
    stored_at, items = entry
    if time.monotonic() - stored_at > _FEED_TTL_SECONDS:
        _feed_cache.pop(_cache_key(ctx), None)
        return None
    return items


def _pick_cached_item(text: str, items: list[FeedItem] | None) -> FeedItem | None:
    if not items or not text.isdigit():
        return None
    index = int(text)
    if 1 <= index <= len(items):
        return items[index - 1]
    return None


def _format_abstime(value: int) -> str:
    if value <= 0:
        return "-"
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(value))
    except (OSError, ValueError):
        return "-"


def _truncate(text: str, limit: int) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return f"{value[: limit - 1]}…"


async def _reply_failure(ctx: CommandContext, exc: BaseException, *, action: str) -> None:
    detail = str(exc).strip() or type(exc).__name__
    logger.warning("[QZone] %s 失败: %s", action, detail)
    key = "qzone.login_failed" if isinstance(exc, service.QzoneLoginError) else "qzone.action_failed"
    await ctx.send(msg(key, detail=detail[:160]))
