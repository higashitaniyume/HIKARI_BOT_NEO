from __future__ import annotations

from typing import Any

from plugins import sticker_inbox
from plugins import sticker_library
from plugins import voice_library

def _split_keywords(value: Any) -> list[str]:
    return sticker_library.split_keywords(value)


def _register_trigger(pack_name: str, keyword: str = "") -> None:
    sticker_library.register_pack_keywords(pack_name, keyword, include_pack_name=True)


def _add_trigger_keyword(pack_name: str, keyword: str) -> None:
    sticker_library.add_keywords(pack_name, keyword)


def _remove_trigger_keyword(pack_name: str, keyword: str) -> bool:
    return sticker_library.remove_keyword(pack_name, keyword)

def _pack_state() -> dict[str, Any]:
    return sticker_library.get_state()


def _pack_detail_state(pack_name: str) -> dict[str, Any]:
    detail = sticker_library.get_pack_detail(pack_name)
    if detail is None:
        raise ValueError("没有找到这个贴纸包。")
    return {"pack": detail}

def _inbox_state() -> dict[str, Any]:
    return {"items": sticker_inbox.list_items()}


def _collect_page_state(user_id: str) -> dict[str, Any] | None:
    """定向收集公开页面数据。未配置/已禁用时返回 None。

    目标已配置但贴纸包还不存在（尚未收集到表情包）时返回空状态，
    页面显示"还没有收集到表情包"而不是 404。
    """
    from plugins.sticker_collector.config import get_target

    target = get_target(user_id)
    if target is None or not target.get("enabled", True):
        return None
    detail = sticker_library.get_pack_detail(target["pack"])
    if detail is None:
        return {
            "user_id": user_id,
            "name": str(target.get("name") or "").strip() or user_id,
            "pack": str(target.get("pack") or "").strip(),
            "count": 0,
            "stickers": [],
        }

    stickers = [sticker for sticker in detail.get("stickers") or [] if not sticker.get("missing")]
    stickers.sort(key=lambda sticker: int(sticker.get("created_at") or 0), reverse=True)
    return {
        "user_id": user_id,
        "name": str(target.get("name") or "").strip() or user_id,
        "pack": detail["name"],
        "count": len(stickers),
        "stickers": stickers,
    }


def _collect_page_has_sticker(user_id: str, sticker_id: str) -> bool:
    state = _collect_page_state(user_id)
    return bool(state and any(sticker.get("id") == sticker_id for sticker in state["stickers"]))


def _voice_state() -> dict[str, Any]:
    return voice_library.get_state()


