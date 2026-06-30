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


def _voice_state() -> dict[str, Any]:
    return voice_library.get_state()


