"""出站自审查：机器人发消息前先让 AI 看一眼，命中极度政治敏感内容就拦下。

挂在 NoneBot 的 calling_api hook 上，对所有插件的发送调用生效，不需要改任何
发送方。审查会短暂阻塞这一次发送——这是「发之前审查」的固有代价，所以超时、
异常、模型未配置一律放行（fail-open），审查坏掉不会让机器人失声。

相同文本只问模型一次（LRU 缓存），帮助文本这类固定回复因此几乎不产生费用。
AI 费用由机器人主人承担。
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import logging
from collections import OrderedDict
from typing import Any

from nonebot.adapters.onebot.v11 import Bot
from nonebot.exception import MockApiException

from core.access_control import normalize_access_rules
from core.bot_messages import get_message
from core.error_notifier import notify_superuser_message
from plugins.aiagent.review import ReviewResult, request_verdict

from .config import get_config
from .extract import api_kind, extract_text, payload_of

logger = logging.getLogger("HikariBot.SelfReview")

# 审查自己发出的提示 / 上报时置位，避免 hook 递归审查自己的通知。
_bypass: contextvars.ContextVar[bool] = contextvars.ContextVar("self_review_bypass", default=False)

_semaphore: asyncio.Semaphore | None = None
_semaphore_size = 0
_cache: OrderedDict[str, ReviewResult] = OrderedDict()
_pending: set[asyncio.Task[None]] = set()

logger.info("[SelfReview] 出站自审查插件已加载")


@Bot.on_calling_api
async def review_outgoing_message(bot: Bot, api: str, data: dict[str, Any]) -> None:
    if _bypass.get():
        return
    kind = api_kind(api)
    if not kind:
        return

    cfg = get_config()
    review_cfg = cfg["review"]
    if not cfg["enabled"] or not review_cfg["enabled"]:
        return
    if kind == "forward" and not cfg["scope"]["forward"]:
        return

    group_id = str(data.get("group_id") or "").strip()
    user_id = str(data.get("user_id") or "").strip()
    if group_id and not cfg["scope"]["group"]:
        return
    if not group_id and not cfg["scope"]["private"]:
        return
    if not _target_under_review(cfg, group_id, user_id):
        return

    text = extract_text(payload_of(data, kind)).strip()
    if len(text) < review_cfg["min_chars"]:
        return

    result = await _verdict(text[: review_cfg["max_chars"]], review_cfg)
    if result is None or not result.risk:
        return

    logger.warning(
        "[SelfReview] 命中敏感内容 api=%s group=%s user=%s reason=%r",
        api,
        group_id or "-",
        user_id or "-",
        result.reason,
    )
    if not cfg["action"]["block"]:
        return

    _spawn(_report(bot, cfg, api, group_id, user_id, text, result))
    raise MockApiException(None)


def _target_under_review(cfg: dict[str, Any], group_id: str, user_id: str) -> bool:
    """白名单启用 = 只审查名单内会话；黑名单命中 = 跳过；都没启用 = 全审查。"""
    rules = normalize_access_rules(cfg.get("permissions", {}))
    dimension = "group" if group_id else "user"
    target = group_id or user_id
    blacklist = rules["blacklist"]
    whitelist = rules["whitelist"]
    if _dimension_enabled(blacklist, dimension) and target in blacklist[dimension]:
        return False
    if _dimension_enabled(whitelist, dimension):
        return bool(target) and target in whitelist[dimension]
    return True


def _dimension_enabled(list_cfg: dict[str, Any], dimension: str) -> bool:
    key = f"{dimension}_enable"
    if key in list_cfg:
        return bool(list_cfg[key])
    return bool(list_cfg.get("enable", False))


async def _verdict(text: str, review_cfg: dict[str, Any]) -> ReviewResult | None:
    cache_size = review_cfg["cache_size"]
    key = _cache_key(text, review_cfg["prompt"]) if cache_size else ""
    if key and key in _cache:
        _cache.move_to_end(key)
        return _cache[key]

    try:
        async with _get_semaphore(review_cfg["max_concurrent"]):
            result = await asyncio.wait_for(
                request_verdict(text, review_cfg, label="SelfReview"),
                timeout=review_cfg["timeout_seconds"] + 5,
            )
    except asyncio.TimeoutError:
        logger.warning("[SelfReview] 审查超时，放行本次发送")
        return None
    except Exception as e:
        logger.warning("[SelfReview] 审查失败，放行本次发送: %s", e)
        return None

    if result is not None and key:
        _cache[key] = result
        _cache.move_to_end(key)
        while len(_cache) > cache_size:
            _cache.popitem(last=False)
    return result


async def _report(
    bot: Bot,
    cfg: dict[str, Any],
    api: str,
    group_id: str,
    user_id: str,
    text: str,
    result: ReviewResult,
) -> None:
    token = _bypass.set(True)
    try:
        if cfg["action"]["notify_chat"]:
            await _notify_chat(bot, group_id, user_id)
        if cfg["action"]["notify_superuser"]:
            await notify_superuser_message(
                bot,
                get_message(
                    "self_review.risk_superuser_notify",
                    api=api,
                    target=f"群 {group_id}" if group_id else (f"私聊 {user_id}" if user_id else "未知会话"),
                    reason=result.reason or "未提供",
                    text=text[:300],
                ),
            )
    except Exception as e:
        logger.warning("[SelfReview] 拦截上报失败: %s", e)
    finally:
        _bypass.reset(token)


async def _notify_chat(bot: Bot, group_id: str, user_id: str) -> None:
    notice = get_message("self_review.risk_chat_notice")
    if group_id:
        await bot.send_group_msg(group_id=int(group_id), message=notice)
    elif user_id:
        await bot.send_private_msg(user_id=int(user_id), message=notice)


def _cache_key(text: str, prompt: str) -> str:
    digest = hashlib.sha256()
    digest.update(prompt.encode("utf-8", "replace"))
    digest.update(b"\x00")
    digest.update(text.encode("utf-8", "replace"))
    return digest.hexdigest()


def _spawn(coro: Any) -> None:
    task = asyncio.create_task(coro)
    _pending.add(task)
    task.add_done_callback(_pending.discard)


def _get_semaphore(size: int) -> asyncio.Semaphore:
    global _semaphore, _semaphore_size
    if _semaphore is None or _semaphore_size != size:
        _semaphore = asyncio.Semaphore(size)
        _semaphore_size = size
    return _semaphore
