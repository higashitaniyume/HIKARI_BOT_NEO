"""QQ 空间插件：用 NapCat 当前登录的 QQ 发说说、看动态、点赞和评论。"""

from __future__ import annotations

import logging
import time
from typing import Any

from core.access_control import is_event_allowed
from core.bot_messages import get_message as msg
from core.command_router import CommandContext, command

from . import ai_tools, schedule, service  # noqa: F401  (ai_tools/schedule 负责注册工具与定时循环)
from .config import VISIBLE_LABELS, VISIBLE_VALUES, get_config
from .service import CommentItem, FeedItem, MoodItem

logger = logging.getLogger("HikariBot.QZone")

_WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 「空间动态」结果缓存：会话 -> (写入时间, 动态列表)，供后续按序号点赞/评论
_FEED_TTL_SECONDS = 600.0
_FEED_CACHE_LIMIT = 200
_feed_cache: dict[str, tuple[float, list[FeedItem]]] = {}

# 「我的说说」缓存：会话 -> (写入时间, 说说列表)，供评论列表/回复评论按序号选帖
_MY_POSTS_TTL_SECONDS = 600.0
_my_posts_cache: dict[str, tuple[float, list[MoodItem]]] = {}

# 「评论列表」缓存：会话 -> (写入时间, 帖子, 评论列表)，供回复评论按序号选评论
_COMMENTS_TTL_SECONDS = 600.0
_comment_cache: dict[str, tuple[float, MoodItem, list[CommentItem]]] = {}

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
    "我的说说",
    aliases=("我的空间",),
    description="查看自己最近发的说说与评论数",
    usage="我的说说 [数量]",
    category="空间",
)
async def handle_my_posts(ctx: CommandContext) -> None:
    cfg = get_config()
    if not cfg["enabled"] or not is_event_allowed(cfg, ctx.event):
        return

    count = _parse_count(ctx.args, default=cfg["moods_count"], maximum=20)
    if count is None:
        await ctx.send(msg("qzone.moods_usage"))
        return

    try:
        items = await _load_my_posts(ctx, cfg, count=count)
    except (service.QzoneLoginError, service.QzoneActionError) as exc:
        await _reply_failure(ctx, exc, action="my_posts")
        return

    if not items:
        await ctx.send(msg("qzone.my_posts_empty"))
        return

    lines = [msg("qzone.my_posts_header", count=len(items))]
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
    lines.append(msg("qzone.my_posts_footer"))
    await ctx.send("\n".join(lines))


@command(
    "评论列表",
    aliases=("说说评论列表",),
    description="查看自己某条说说下的评论",
    usage="评论列表 [序号]",
    category="空间",
)
async def handle_comment_list(ctx: CommandContext) -> None:
    cfg = get_config()
    if not cfg["enabled"] or not is_event_allowed(cfg, ctx.event):
        return

    text = ctx.args.strip()
    if text and not text.isdigit():
        await ctx.send(msg("qzone.comments_usage"))
        return

    try:
        posts = _get_cached_my_posts(ctx) or await _load_my_posts(ctx, cfg)
    except (service.QzoneLoginError, service.QzoneActionError) as exc:
        await _reply_failure(ctx, exc, action="comments")
        return

    if not posts:
        await ctx.send(msg("qzone.my_posts_empty"))
        return

    index = int(text) if text else 1
    if not 1 <= index <= len(posts):
        await ctx.send(msg("qzone.my_posts_index_invalid"))
        return
    post = posts[index - 1]

    try:
        comments = await service.fetch_comments(
            ctx.bot, str(ctx.bot.self_id), post.tid, num=20, timeout=cfg["timeout"]
        )
    except (service.QzoneLoginError, service.QzoneActionError) as exc:
        await _reply_failure(ctx, exc, action="comment_list")
        return

    _store_comments(ctx, post, comments)
    if not comments:
        await ctx.send(msg("qzone.comments_empty"))
        return

    lines = [msg("qzone.comments_header", count=len(comments), tid=post.tid)]
    for comment_index, comment in enumerate(comments, 1):
        template = "qzone.comment_row_reply" if comment.depth > 0 else "qzone.comment_row"
        lines.append(
            msg(
                template,
                index=comment_index,
                nickname=comment.nickname,
                content=_truncate(comment.content, 60),
            )
        )
    lines.append(msg("qzone.comments_footer"))
    await ctx.send("\n".join(lines))


@command(
    "回复评论",
    aliases=("回复说说评论",),
    description="回复自己说说下的某条评论",
    usage="回复评论 <评论序号> <内容>",
    category="空间",
    superuser_only=True,
)
async def handle_reply_comment(ctx: CommandContext) -> None:
    cfg = get_config()
    if not cfg["enabled"] or not is_event_allowed(cfg, ctx.event):
        return

    index_text, _, content = ctx.args.strip().partition(" ")
    content = content.strip()
    cached = _get_cached_comments(ctx)
    if cached is None:
        await ctx.send(msg("qzone.comments_cache_miss"))
        return
    post, comments = cached

    position = _parse_index(index_text.strip(), len(comments))
    if position is None:
        await ctx.send(msg("qzone.comment_index_invalid"))
        return
    if not content:
        await ctx.send(msg("qzone.reply_usage"))
        return
    if len(content) > cfg["max_length"]:
        await ctx.send(msg("qzone.text_too_long", limit=cfg["max_length"]))
        return

    target = comments[position - 1]
    try:
        await service.reply_comment(
            ctx.bot,
            str(ctx.bot.self_id),
            post.tid,
            target,
            content,
            timeout=cfg["timeout"],
        )
    except (service.QzoneLoginError, service.QzoneActionError) as exc:
        await _reply_failure(ctx, exc, action="reply_comment")
        return

    logger.info(
        "[QZone] 已回复评论 uin=%s tid=%s comment_id=%s reply_uin=%s len=%d",
        ctx.bot.self_id,
        post.tid,
        target.comment_id,
        target.uin,
        len(content),
    )
    await ctx.send(msg("qzone.reply_ok", nickname=target.nickname))


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


@command(
    "空间定时",
    aliases=("空间计划",),
    description="查看或手动触发空间定时说说",
    usage="空间定时 [run <任务id>]",
    detail_key="qzone.schedule_help",
    category="空间",
    superuser_only=True,
)
async def handle_schedule(ctx: CommandContext) -> None:
    cfg = get_config()
    if not cfg["enabled"] or not is_event_allowed(cfg, ctx.event):
        return

    args = ctx.args.strip()
    if args.startswith("run ") or args.startswith("跑 "):
        job_id = args.split(" ", 1)[1].strip()
        job = schedule.find_job(job_id)
        if job is None:
            await ctx.send(msg("qzone.schedule_unknown_job", job_id=job_id or "-"))
            return
        try:
            result = await schedule.run_job(job, cfg=cfg, manual=True)
        except (service.QzoneLoginError, service.QzoneActionError, RuntimeError) as exc:
            await _reply_failure(ctx, exc, action="schedule_run")
            return
        await ctx.send(
            msg("qzone.schedule_run_ok", job_id=job_id, tid=result.get("tid") or "-")
        )
        return

    snapshot = schedule.status_snapshot()
    lines = [
        msg(
            "qzone.schedule_header",
            state="开启" if snapshot["enabled"] else "关闭",
            source="AI 生成" if snapshot["ai_enabled"] else "固定文案",
            count=len(snapshot["jobs"]),
        )
    ]
    if not snapshot["jobs"]:
        lines.append(msg("qzone.schedule_empty"))
    for job in snapshot["jobs"]:
        lines.append(
            msg(
                "qzone.schedule_job_line",
                id=job["id"],
                time=job["time"],
                days=_format_days(job["days"]),
                visible=job["visible_label"],
                next_run=job["next_run"],
                last=job["last_date"] or msg("qzone.schedule_never_sent"),
                error=f"（{job['error']}）" if job["error"] else "",
            )
        )
    lines.append(msg("qzone.schedule_footer"))
    await ctx.send("\n".join(lines))


# ── 参数与缓存 ─────────────────────────────────────────────


def _format_days(days: list[int]) -> str:
    if not days:
        return msg("qzone.schedule_every_day")
    return "、".join(_WEEKDAY_LABELS[day] for day in days if 0 <= day < len(_WEEKDAY_LABELS))


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


async def _load_my_posts(
    ctx: CommandContext,
    cfg: dict[str, object],
    *,
    count: int | None = None,
) -> list[MoodItem]:
    target = count if count is not None else int(cfg["moods_count"])  # type: ignore[arg-type]
    items = await service.fetch_moods(
        ctx.bot,
        str(ctx.bot.self_id),
        target,
        timeout=int(cfg["timeout"]),  # type: ignore[arg-type]
    )
    _store_my_posts(ctx, items)
    return items


def _store_my_posts(ctx: CommandContext, items: list[MoodItem]) -> None:
    if len(_my_posts_cache) >= _FEED_CACHE_LIMIT:
        oldest = min(_my_posts_cache, key=lambda key: _my_posts_cache[key][0])
        _my_posts_cache.pop(oldest, None)
    _my_posts_cache[_cache_key(ctx)] = (time.monotonic(), list(items))


def _get_cached_my_posts(ctx: CommandContext) -> list[MoodItem] | None:
    entry = _my_posts_cache.get(_cache_key(ctx))
    if entry is None:
        return None
    stored_at, items = entry
    if time.monotonic() - stored_at > _MY_POSTS_TTL_SECONDS:
        _my_posts_cache.pop(_cache_key(ctx), None)
        return None
    return items


def _store_comments(ctx: CommandContext, post: MoodItem, comments: list[CommentItem]) -> None:
    if len(_comment_cache) >= _FEED_CACHE_LIMIT:
        oldest = min(_comment_cache, key=lambda key: _comment_cache[key][0])
        _comment_cache.pop(oldest, None)
    _comment_cache[_cache_key(ctx)] = (time.monotonic(), post, list(comments))


def _get_cached_comments(ctx: CommandContext) -> tuple[MoodItem, list[CommentItem]] | None:
    entry = _comment_cache.get(_cache_key(ctx))
    if entry is None:
        return None
    stored_at, post, comments = entry
    if time.monotonic() - stored_at > _COMMENTS_TTL_SECONDS:
        _comment_cache.pop(_cache_key(ctx), None)
        return None
    return post, comments


def _parse_index(text: str, upper: int) -> int | None:
    value = str(text or "").strip()
    if not value.isdigit():
        return None
    index = int(value)
    return index if 1 <= index <= upper else None


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
