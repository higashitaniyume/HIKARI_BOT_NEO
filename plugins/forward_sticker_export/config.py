from __future__ import annotations

from typing import Any

from core.config_loader import load_plugin_config

PLUGIN_NAME = "forward_sticker_export"

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    # 只有超级管理员 / 群主 / 群管理员可以用「导出表情」命令（写入共享贴纸库）。
    # 设为 False 则所有人可用。
    "require_admin": True,
    # 递归展开合并转发的最大层数（防止畸形/超深嵌套拖死）。
    "max_depth": 6,
    # 单次最多导出的图片数量上限。
    "max_images": 300,
    # 只导出 QQ 表情面板的动画表情（summary == "[动画表情]"）。
    # 设为 False 则合并记录里的所有图片都会被导出。
    "animated_only": True,
    # 未显式指定包名时的默认贴纸包名。
    "default_pack": "合并转发导出",
    # 下载/转码相关。
    "temp_root": "/tmp/hikari_bot/forward_sticker_export",
    "download_timeout_seconds": 30,
    "max_download_mb": 30,
    # 下载 + 转码并发数。
    "concurrency": 3,
    # get_forward_msg 单次调用超时（秒）。
    "forward_api_timeout_seconds": 30,
}


def get_config() -> dict[str, Any]:
    return load_plugin_config(PLUGIN_NAME, DEFAULT_CONFIG)
