"""把手牌快照格式化成一条 QQ 文本消息。

纯文本输出；长度按 `max_chars` 收敛，优先保留玩家基础信息和卡牌名称，
空间不足时先裁掉卡牌描述，再裁掉末尾多余的卡牌。
"""

from __future__ import annotations

import re

from .models import SpectatorCard, SpectatorPlayer, SpectatorSnapshot

_WHITESPACE_RE = re.compile(r"\s+")

# 顶部结构行（观战码、房间号、人数、隐藏提示）需要预留的字符数。
_HEADING_BUDGET = 160
_OMIT_NOTICE = "…还有 {count} 名玩家的手牌因长度限制未显示。"


def _compact(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", str(value or "")).strip()


def _truncate(value: str, limit: int) -> str:
    text = str(value or "")
    if limit <= 0 or len(text) <= limit:
        return text
    if limit == 1:
        return "…"
    return f"{text[: limit - 1]}…"


def _stat_text(player: SpectatorPlayer) -> str:
    parts: list[str] = []
    if player.hp is not None:
        if player.max_hp is not None:
            parts.append(f"HP {player.hp}/{player.max_hp}")
        else:
            parts.append(f"HP {player.hp}")
    if player.gold is not None:
        parts.append(f"金币 {player.gold}")
    if player.attack is not None:
        parts.append(f"攻击 {player.attack}")
    if player.defense is not None:
        parts.append(f"防御 {player.defense}")
    return " · ".join(parts)


def _hand_label(player: SpectatorPlayer) -> str:
    count = f"共 {player.hand_count} 张" if player.hand_count is not None else "张数未知"
    if player.hand_known:
        return f"手牌（{count}）"
    if player.hand_count is not None:
        return f"手牌（{count}）：服务器只提供数量，牌面被遮蔽"
    return "手牌：服务器未提供牌面"


def _card_tag(card: SpectatorCard) -> str:
    if card.purify_num > 0:
        return f"[战斗{card.battle_cost}·净化{card.purify_num}]"
    return f"[战斗{card.battle_cost}]"


def _card_text(
    card: SpectatorCard,
    *,
    with_description: bool,
    max_name_chars: int,
    max_description_chars: int,
) -> str:
    name = _truncate(_compact(card.name) or f"卡牌 {card.card_id}", max_name_chars)
    text = f"{_card_tag(card)} {name}"
    if with_description:
        description = _truncate(_compact(card.description), max_description_chars)
        if description:
            text = f"{text}｜{description}"
    return text


def _player_block(
    player: SpectatorPlayer,
    *,
    with_description: bool,
    max_name_chars: int,
    max_description_chars: int,
) -> str:
    nick = _truncate(_compact(player.nick) or player.player_id or "未知玩家", max_name_chars)
    header = f"【{player.slot + 1}】{nick}"
    if player.hero_name:
        header = f"{header}（{player.hero_name}）"

    lines = [header]
    stats = _stat_text(player)
    if stats:
        lines.append(f"  {stats}")
    lines.append(f"  {_hand_label(player)}")
    for card in player.cards:
        lines.append(
            "  · "
            + _card_text(
                card,
                with_description=with_description,
                max_name_chars=max_name_chars,
                max_description_chars=max_description_chars,
            )
        )
    return "\n".join(lines)


def format_hand_message(
    snapshot: SpectatorSnapshot,
    *,
    watch_code: str = "",
    max_chars: int = 3000,
    max_name_chars: int = 40,
    max_description_chars: int = 60,
) -> str:
    """渲染观战手牌文本。"""
    budget = max(200, int(max_chars or 3000))
    name_chars = max(4, int(max_name_chars or 40))
    description_chars = max(0, int(max_description_chars or 60))

    players = list(snapshot.players or [])
    lines: list[str] = []
    if watch_code:
        lines.append(f"【吉星派对观战】{watch_code}")
    else:
        lines.append("【吉星派对观战】")
    if snapshot.room_id:
        lines.append(f"房间 {snapshot.room_id} · {len(players)} 名玩家")
    else:
        lines.append(f"{len(players)} 名玩家")

    heading = "\n".join(lines)
    remaining = budget - len(heading) - 2 - _HEADING_BUDGET

    blocks: list[str] = []
    shown = 0
    for player in players:
        detailed = _player_block(
            player,
            with_description=True,
            max_name_chars=name_chars,
            max_description_chars=description_chars,
        )
        if len(detailed) <= remaining:
            blocks.append(detailed)
            remaining -= len(detailed) + 1
            shown += 1
            continue
        brief = _player_block(
            player,
            with_description=False,
            max_name_chars=name_chars,
            max_description_chars=description_chars,
        )
        if len(brief) <= remaining:
            blocks.append(brief)
            remaining -= len(brief) + 1
            shown += 1
            continue
        # 这名玩家连最简形式都放不下：能塞下就截断显示，之后只做计数提示。
        if remaining >= 32:
            blocks.append(_truncate(brief, remaining))
            shown += 1
        break

    omitted = len(players) - shown
    if omitted > 0:
        blocks.append(_OMIT_NOTICE.format(count=omitted))

    if not blocks:
        blocks.append("没有可显示的玩家手牌数据。")

    return f"{heading}\n\n" + "\n\n".join(blocks)


__all__ = ["format_hand_message"]
