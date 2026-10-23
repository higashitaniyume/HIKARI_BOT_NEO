"""AI Agent 回复跟随表情包模块。

机制：
- 文字回复成功后，从配置的贴纸包中随机抽取 1 张 GIF 跟随发送；
- 长回复走合并转发时，整条回复只跟随 1 张，不按节点发送；
- 失败静默（仅记 warning/debug 日志），绝不影响正常文字回复与事件生命周期；
- 最近 5 张去重，避免连续发送重复表情；
- 使用现有 `plugins/sticker_library` 的统一索引。
"""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Any

from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment

from .utils import safe_bool, safe_float, safe_int

logger = logging.getLogger("HikariBot.AIAgent.Sticker")

_RECENT_LIMIT = 5
# 会话去重：session_id -> list[str]（记录文件绝对路径）
_recent_sent: dict[str, list[str]] = {}


def _sticker_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    chat_cfg = cfg.get("chat") if isinstance(cfg.get("chat"), dict) else {}
    section = chat_cfg.get("sticker")
    return section if isinstance(section, dict) else {}


def enabled(cfg: dict[str, Any]) -> bool:
    return safe_bool(_sticker_cfg(cfg).get("enabled"), False)


def _get_session_id(event: MessageEvent) -> str:
    group_id = getattr(event, "group_id", None)
    if group_id is not None:
        return f"group:{group_id}"
    user_id = getattr(event, "user_id", None)
    if user_id is None:
        try:
            user_id = event.get_user_id()
        except Exception:
            user_id = "default"
    return f"private:{user_id}"


def _get_candidate_stickers(packs: list[str]) -> list[Path]:
    try:
        from plugins.sticker_library import get_all_files, get_packs_files
    except ImportError:
        logger.warning("[AIAgent] sticker_library 模块未找到，无法发送贴纸")
        return []

    if not packs:
        return get_all_files()

    files = get_packs_files(packs)
    if not files:
        logger.warning("[AIAgent] 指定的贴纸包 %s 不存在或为空，跳过贴纸发送", packs)
        return []
    return files


async def maybe_send_sticker(
    bot: Bot,
    event: MessageEvent,
    cfg: dict[str, Any],
    is_auto_reply: bool = False,
) -> bool:
    """在文字回复成功后按配置概率发送 1 张贴纸。"""
    stk_cfg = _sticker_cfg(cfg)
    if not safe_bool(stk_cfg.get("enabled"), False):
        return False

    if is_auto_reply and not safe_bool(stk_cfg.get("allow_auto_reply"), True):
        return False

    probability = safe_float(stk_cfg.get("probability"), 1.0, minimum=0.0, maximum=1.0)
    if probability <= 0.0 or (probability < 1.0 and random.random() > probability):
        return False

    raw_packs = stk_cfg.get("packs")
    packs = [str(p).strip() for p in raw_packs if str(p).strip()] if isinstance(raw_packs, list) else []

    candidates = _get_candidate_stickers(packs)
    if not candidates:
        return False

    session_id = _get_session_id(event)
    recent = _recent_sent.get(session_id, [])

    # 过滤掉最近发送过的 5 张
    available = [p for p in candidates if str(p.resolve()) not in recent]
    if not available:
        # 如果全部都在最近记录中，清空旧记录并重新选择
        available = candidates
        recent = []

    chosen = random.choice(available)
    chosen_str = str(chosen.resolve())

    # 更新最近发送记录
    recent.append(chosen_str)
    if len(recent) > _RECENT_LIMIT:
        recent = recent[-_RECENT_LIMIT:]
    _recent_sent[session_id] = recent

    try:
        uri = chosen.resolve().as_uri()
        await bot.send(event, Message(MessageSegment.image(uri)))
        return True
    except Exception as e:
        logger.warning("[AIAgent] 发送跟随贴纸失败: %s", e)
        return False
