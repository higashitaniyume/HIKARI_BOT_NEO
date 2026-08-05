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


def _public_pack_state(key: str) -> dict[str, Any] | None:
    """公开贴纸包页面数据。key 可以是定向收集目标 QQ 号或贴纸包名。

    - 命中定向收集目标 → 显示该目标的贴纸包（目标已配置但包尚未创建时返回空状态）
    - 未命中目标 → 按贴纸包名查找，包不存在返回 None（404）
    """
    from plugins.sticker_collector.config import get_target

    target = get_target(key)
    if target is not None and target.get("enabled", True):
        pack = str(target.get("pack") or "").strip()
        name = str(target.get("name") or "").strip() or key
        if not pack:
            return None
        detail = sticker_library.get_pack_detail(pack)
        if detail is None:
            return {
                "user_id": key,
                "name": name,
                "pack": pack,
                "count": 0,
                "stickers": [],
            }
    else:
        pack = key
        name = key
        detail = sticker_library.get_pack_detail(pack)
        if detail is None:
            return None

    stickers = [sticker for sticker in detail.get("stickers") or [] if not sticker.get("missing")]
    stickers.sort(key=lambda sticker: int(sticker.get("created_at") or 0), reverse=True)
    return {
        "user_id": key,
        "name": name,
        "pack": detail["name"],
        "count": len(stickers),
        "stickers": stickers,
    }


def _public_pack_has_sticker(key: str, sticker_id: str) -> bool:
    state = _public_pack_state(key)
    return bool(state and any(sticker.get("id") == sticker_id for sticker in state["stickers"]))


def _voice_state() -> dict[str, Any]:
    return voice_library.get_state()


