"""
贴纸库包。

管理贴纸索引、关键词和贴纸文件的存储与查询。

公开 API 在 __init__.py 中 re-export，内部分为 index.py（索引管理）和
operations.py（增删改查）。
"""

from __future__ import annotations

from .index import (
    MEDIA_EXTS,
    PACK_PREVIEW_LIMIT,
    STORAGE_ROOT,
    load_index,
    save_index,
)
from .operations import (
    add_keywords,
    count_pack,
    delete_pack,
    get_all_files,
    get_files_for_keyword,
    get_keyword_map,
    get_pack_archive_files,
    get_pack_detail,
    get_pack_files,
    get_packs_files,
    get_state,
    get_sticker_path,
    list_pack_names,
    move_stickers_between_packs,
    register_pack_keywords,
    remove_keyword,
    remove_stickers_from_pack,
    safe_pack_name,
    save_gif_to_pack,
    save_gifs_to_pack,
    split_keywords,
)

__all__ = [
    "MEDIA_EXTS",
    "PACK_PREVIEW_LIMIT",
    "STORAGE_ROOT",
    "load_index",
    "save_index",
    "split_keywords",
    "safe_pack_name",
    "list_pack_names",
    "get_pack_files",
    "get_sticker_path",
    "get_packs_files",
    "get_all_files",
    "count_pack",
    "get_pack_detail",
    "get_pack_archive_files",
    "remove_stickers_from_pack",
    "move_stickers_between_packs",
    "get_keyword_map",
    "get_files_for_keyword",
    "get_state",
    "register_pack_keywords",
    "add_keywords",
    "remove_keyword",
    "delete_pack",
    "save_gifs_to_pack",
    "save_gif_to_pack",
]
