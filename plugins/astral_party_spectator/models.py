"""AstralPartySpectator `/query` 响应的数据模型。

字段与 docs/API.md 描述的一致：`handKnown=true` 时 `cards` 才是服务器校验过的
完整牌面；`handKnown=false` 时 `cards` 一定为空，不允许据数量推断牌面。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


@dataclass(slots=True)
class SpectatorCard:
    card_uid: int = 0
    card_id: int = 0
    purify_num: int = 0
    is_temp: bool = False
    battle_cost: int = 0
    base_cost: int = 0
    name: str = ""
    description: str = ""

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SpectatorCard":
        battle_cost = _as_int(payload.get("battleCost"))
        base_cost = _as_int(payload.get("baseCost"))
        return cls(
            card_uid=_as_int(payload.get("cardUid")) or 0,
            card_id=_as_int(payload.get("cardId")) or 0,
            purify_num=_as_int(payload.get("purifyNum")) or 0,
            is_temp=bool(payload.get("isTemp")),
            # 上游在战斗费用未知时会给 -1，此时回落到卡牌基础费用。
            battle_cost=battle_cost if battle_cost is not None and battle_cost >= 0 else (base_cost or 0),
            base_cost=base_cost or 0,
            name=_as_text(payload.get("name")),
            description=_as_text(payload.get("description")),
        )


@dataclass(slots=True)
class SpectatorPlayer:
    player_id: str = ""
    slot: int = 0
    nick: str = ""
    hero_id: int | None = None
    hero_name: str = ""
    hp: int | None = None
    max_hp: int | None = None
    gold: int | None = None
    attack: int | None = None
    defense: int | None = None
    hand_known: bool = False
    hand_count: int | None = None
    hand_reason: str = ""
    cards: list[SpectatorCard] = field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SpectatorPlayer":
        raw_cards = payload.get("cards")
        cards = [
            SpectatorCard.from_payload(item)
            for item in (raw_cards if isinstance(raw_cards, list) else [])
            if isinstance(item, dict)
        ]
        hand_known = bool(payload.get("handKnown"))
        return cls(
            player_id=_as_text(payload.get("playerId")),
            slot=_as_int(payload.get("slot")) or 0,
            nick=_as_text(payload.get("nick")),
            hero_id=_as_int(payload.get("heroId")),
            hero_name=_as_text(payload.get("heroName")),
            hp=_as_int(payload.get("hp")),
            max_hp=_as_int(payload.get("maxHp")),
            gold=_as_int(payload.get("gold")),
            attack=_as_int(payload.get("attack")),
            defense=_as_int(payload.get("defense")),
            hand_known=hand_known,
            hand_count=_as_int(payload.get("handCount")),
            hand_reason=_as_text(payload.get("handReason")),
            # 服务器只给数量时不允许保留牌面。
            cards=cards if hand_known else [],
        )


@dataclass(slots=True)
class SpectatorSnapshot:
    mode: str = ""
    status: str = ""
    room_id: str = ""
    player_id: str = ""
    hand_count: int | None = None
    players: list[SpectatorPlayer] = field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SpectatorSnapshot":
        raw_players = payload.get("players")
        players = [
            SpectatorPlayer.from_payload(item)
            for item in (raw_players if isinstance(raw_players, list) else [])
            if isinstance(item, dict)
        ]
        players.sort(key=lambda player: player.slot)
        return cls(
            mode=_as_text(payload.get("mode")),
            status=_as_text(payload.get("status")),
            room_id=_as_text(payload.get("roomId")),
            player_id=_as_text(payload.get("playerId")),
            hand_count=_as_int(payload.get("handCount")),
            players=players,
        )
