"""认证提供者自动解析：降低使用门槛。"""
from __future__ import annotations

import os
import sys

from .auto import AutoAuthProvider
from .base import AuthProvider
from .cookie import CookieFileProvider, ManualCookieProvider, default_cookie_file
from .napcat import NapCatAuthProvider
from .qrcode import QRCodeLoginProvider

_NAP_DEFAULT = "ws://localhost:3001"
_NAP_PROBE_TIMEOUT = 5  # NapCat 在线探测超时（秒）


def resolve_provider(
    *,
    cookie: str | None = None,
    uin: str | None = None,
    cookie_file: str | None = None,
    ws_url: str | None = None,
    interactive: bool = False,
) -> AuthProvider:
    """
    自动解析认证方式，优先级：

    1. 手动 Cookie（QZONE_COOKIE + QZONE_UIN 环境变量）——显式指定，最高优先
    2. 配置了 QQ 号（QZONE_UIN）→ 先探测 NapCat，在线则直接使用
    3. 凭证文件（qzone login --save 生成）
    4. NapCat 兜底
    5. interactive=True 且终端可用时，NapCat 离线自动引导扫码登录

    配置 QQ 号的方法：设置环境变量 QZONE_UIN，例如
    ``set QZONE_UIN=123456789``（Windows）或 ``export QZONE_UIN=123456789``（Linux/macOS）。
    """
    uin = uin or os.environ.get("QZONE_UIN")
    cookie = cookie or os.environ.get("QZONE_COOKIE")
    if cookie and uin:
        return ManualCookieProvider(cookie, uin)

    ws = ws_url or os.environ.get("QZONE_WS_URL") or _NAP_DEFAULT
    candidates: list[AuthProvider] = []
    if uin:
        # 配置了 QQ 号：NapCat 在线优先（短超时探测，避免离线时长时间等待）
        candidates.append(NapCatAuthProvider(ws_url=ws, timeout=_NAP_PROBE_TIMEOUT))

    path = cookie_file or os.environ.get("QZONE_COOKIE_FILE") or default_cookie_file()
    if os.path.exists(path):
        candidates.append(CookieFileProvider(path))

    if not uin:
        candidates.append(NapCatAuthProvider(ws_url=ws))

    if interactive and sys.stdin.isatty():
        candidates.append(QRCodeLoginProvider())

    if not candidates:
        return NapCatAuthProvider(ws_url=ws)
    if len(candidates) == 1:
        return candidates[0]
    return AutoAuthProvider(candidates, preferred_uin=uin)
