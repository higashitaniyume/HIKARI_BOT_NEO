"""
Spire Codex API 相关数据结构和查询辅助函数。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class _SpireCandidate:
    endpoint: str
    item_id: str
    name: str
    summary: str
    extract: str
    exact_name: bool
    score: int


_DEFAULT_SEARCH_CATEGORIES = (
    "cards",
    "characters",
    "relics",
    "potions",
    "powers",
    "keywords",
    "monsters",
    "events",
)

_ENDPOINT_LABELS = {
    "cards": "卡牌",
    "characters": "角色",
    "relics": "遗物",
    "potions": "药水",
    "powers": "能力效果",
    "keywords": "关键词",
    "monsters": "怪物",
    "events": "事件",
    "encounters": "遭遇",
    "acts": "章节",
    "ascensions": "进阶",
    "orbs": "充能球",
    "afflictions": "苦痛",
    "modifiers": "修正",
    "achievements": "成就",
}

_CHARACTER_LABELS = {
    "ironclad": "铁甲战士",
    "silent": "静默猎手",
    "defect": "故障机器人",
    "regent": "储君",
    "necrobinder": "亡灵契约师",
    "shared": "通用",
    "colorless": "无色",
    "token": "衍生",
}


def _search_categories(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return _DEFAULT_SEARCH_CATEGORIES
    categories = [str(item).strip() for item in value if str(item).strip()]
    return tuple(categories) or _DEFAULT_SEARCH_CATEGORIES


def _endpoint_label(endpoint: str) -> str:
    return _ENDPOINT_LABELS.get(endpoint, endpoint)


def _spire_candidate(endpoint: str, item: dict[str, Any], query_key: str, index: int) -> _SpireCandidate | None:
    item_id = str(item.get("id") or "").strip()
    name = str(item.get("name") or "").strip()
    if not item_id or not name:
        return None

    fields = _spire_text_fields(item)
    haystack = _compact_key(" ".join([name, *fields]))
    name_key = _compact_key(name)
    exact_name = bool(query_key and name_key == query_key)
    if query_key and query_key not in haystack:
        return None

    summary = _spire_summary(endpoint, item)
    extract = _spire_extract(endpoint, item)
    score = _spire_score(endpoint, name_key, haystack, query_key, index)
    return _SpireCandidate(
        endpoint=endpoint,
        item_id=item_id,
        name=name,
        summary=summary,
        extract=extract,
        exact_name=exact_name,
        score=score,
    )


def _spire_text_fields(item: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    for key in ("description", "flavor", "type", "rarity", "pool", "color"):
        value = item.get(key)
        if isinstance(value, str):
            fields.append(value)
    tags = item.get("tags")
    if isinstance(tags, list):
        fields.extend(str(tag) for tag in tags)
    return fields


def _spire_score(endpoint: str, name_key: str, haystack: str, query_key: str, index: int) -> int:
    endpoint_rank = list(_DEFAULT_SEARCH_CATEGORIES).index(endpoint) if endpoint in _DEFAULT_SEARCH_CATEGORIES else 99
    score = 1000 - endpoint_rank * 20 - index
    if query_key and name_key == query_key:
        score += 10000
    elif query_key and name_key.startswith(query_key):
        score += 3000
    elif query_key and query_key in name_key:
        score += 1500
    elif query_key and query_key in haystack:
        score += 100
    return score


def _spire_summary(endpoint: str, item: dict[str, Any]) -> str:
    parts = [_endpoint_label(endpoint)]
    if endpoint == "cards":
        parts.extend(
            part
            for part in (
                _character_label(item.get("color")),
                _safe_text(item.get("type")),
                _safe_text(item.get("rarity")),
                _cost_label(item),
            )
            if part
        )
    elif endpoint in {"relics", "potions"}:
        parts.extend(part for part in (_character_label(item.get("pool")), _safe_text(item.get("rarity"))) if part)
    elif endpoint == "characters":
        parts.extend(
            part
            for part in (
                f"生命 {item.get('starting_hp')}" if item.get('starting_hp') is not None else "",
                f"初始金币 {item.get('starting_gold')}" if item.get('starting_gold') is not None else "",
                f"能量 {item.get('max_energy')}" if item.get('max_energy') is not None else "",
            )
            if part
        )
    elif endpoint == "monsters":
        parts.append(_safe_text(item.get("type")))
    return " · ".join(part for part in parts if part)


def _spire_extract(endpoint: str, item: dict[str, Any]) -> str:
    lines = [_spire_summary(endpoint, item)]
    description = _strip_spire_markup(_safe_text(item.get("description")))
    if description:
        lines.append(description)

    if endpoint == "cards":
        upgrade = _strip_spire_markup(_safe_text(item.get("upgrade_description")))
        if upgrade and upgrade != description:
            lines.append(f"升级：{upgrade}")
    flavor = _strip_spire_markup(_safe_text(item.get("flavor")))
    if flavor:
        lines.append(f"描述：{flavor}")
    return "\n".join(line for line in lines if line)


def _safe_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _character_label(value: Any) -> str:
    key = str(value or "").strip().casefold()
    return _CHARACTER_LABELS.get(key, str(value).strip() if value else "")


def _cost_label(item: dict[str, Any]) -> str:
    if item.get("is_x_cost"):
        return "费用 X"
    if item.get("is_x_star_cost"):
        return "星能 X"
    star_cost = item.get("star_cost")
    if star_cost is not None:
        return f"星能 {star_cost}"
    cost = item.get("cost")
    if cost is None:
        return ""
    return f"费用 {cost}"


def _strip_spire_markup(value: str) -> str:
    text = value
    text = re.sub(r"\[energy:(\d+)\]", r"\1费", text)
    text = re.sub(r"\[star:(\d+)\]", r"\1星", text)
    text = re.sub(r"\[/?[a-z]+(?:[:=][^\]]+)?\]", "", text, flags=re.IGNORECASE)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _compact_key(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().casefold())
