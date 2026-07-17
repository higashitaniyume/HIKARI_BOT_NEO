"""QQ profile like command."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable

from nonebot.adapters.onebot.v11.exception import ActionFailed

from core.command_router import CommandContext, command
from core.stats_tracker import increment as stats_increment

from .config import get_config

logger = logging.getLogger("HikariBot.ProfileLike")

_CQ_AT_RE = re.compile(r"\[CQ:at,qq=([^,\]]+)(?:,[^\]]*)?\]")
_NUMBER_RE = re.compile(r"(?<!\d)(\d{1,12})(?!\d)")


@dataclass(frozen=True)
class LikeRequest:
    user_id: int
    times: int
    explicit_target: bool = False


@command(
    "点赞",
    aliases=("赞我", "点满赞", "资料赞", "名片赞", "like"),
    description="给 QQ 资料卡点满赞",
    usage="点赞 [@用户|QQ号] [次数]",
    detail_key="profile_like.help",
)
async def handle_profile_like(ctx: CommandContext) -> None:
    cfg = get_config()
    if not cfg.get("enabled", True):
        return

    try:
        request = parse_like_request(
            ctx.args,
            sender_id=int(ctx.event.get_user_id()),
            at_user_ids=extract_at_user_ids(ctx.event.get_message()),
            default_times=int(cfg.get("default_times", 10)),
            max_times=int(cfg.get("max_times", 10)),
        )
        await send_profile_like(ctx.bot, user_id=request.user_id, times=request.times)
        stats_increment(ctx.event, "profile_likes_given", request.times)
    except ActionFailed as e:
        logger.warning(
            "[ProfileLike] 点赞失败 user_id=%s times=%s info=%s",
            request.user_id,
            request.times,
            getattr(e, "info", e),
        )
        return
    except Exception as e:
        logger.exception("[ProfileLike] 点赞处理异常: %s", e)
        return


async def send_profile_like(bot: Any, *, user_id: int, times: int) -> Any:
    return await bot.call_api("send_like", user_id=user_id, times=times)


def parse_like_request(
    args: str,
    *,
    sender_id: int,
    at_user_ids: Iterable[int] = (),
    default_times: int = 10,
    max_times: int = 10,
) -> LikeRequest:
    max_times = _clamp_int(max_times, minimum=1, maximum=10)
    default_times = _clamp_int(default_times, minimum=1, maximum=max_times)
    text, cq_at_ids = _remove_cq_at(args)

    target_id = next(iter(at_user_ids), None)
    explicit_target = target_id is not None
    if target_id is None and cq_at_ids:
        target_id = cq_at_ids[0]
        explicit_target = True

    numbers = [(match.group(1), int(match.group(1))) for match in _NUMBER_RE.finditer(text)]
    target_token: str | None = None
    if target_id is None:
        for token, value in numbers:
            if len(token) >= 5 or value > max_times:
                target_id = value
                target_token = token
                explicit_target = True
                break

    times: int | None = None
    for token, value in numbers:
        if target_token is not None and token == target_token:
            continue
        if 1 <= value <= max_times:
            times = value
            break

    return LikeRequest(
        user_id=int(target_id if target_id is not None else sender_id),
        times=_clamp_int(times if times is not None else default_times, minimum=1, maximum=max_times),
        explicit_target=explicit_target,
    )


def extract_at_user_ids(message: Iterable[Any]) -> list[int]:
    user_ids: list[int] = []
    for segment in message:
        if getattr(segment, "type", "") != "at":
            continue
        data = getattr(segment, "data", {}) or {}
        user_id = _parse_user_id(data.get("qq"))
        if user_id is not None:
            user_ids.append(user_id)
    return user_ids


def _remove_cq_at(text: str) -> tuple[str, list[int]]:
    user_ids: list[int] = []

    def replace(match: re.Match[str]) -> str:
        user_id = _parse_user_id(match.group(1))
        if user_id is not None:
            user_ids.append(user_id)
        return " "

    return _CQ_AT_RE.sub(replace, str(text or "")), user_ids


def _parse_user_id(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text or text.casefold() == "all" or not text.isdigit():
        return None
    return int(text)


def _clamp_int(value: Any, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = minimum
    return min(max(parsed, minimum), maximum)
