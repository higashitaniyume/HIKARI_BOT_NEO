"""qzone-sdk — 轻量级、零第三方依赖的 QQ 空间操作 SDK。

支持发布/删除/点赞/评论/转发说说、获取好友动态与好友说说；
认证方式：手机 QQ 扫码登录、NapCat WebSocket 自动获取、手动 Cookie。
"""
from .auth import (
    AuthInfo,
    AuthProvider,
    CookieFileProvider,
    ManualCookieProvider,
    NapCatAuthProvider,
    QRCodeLoginProvider,
)
from .client import QZoneClient
from .config import QZoneConfig
from .errors import QZoneApiError, QZoneAuthError, QZoneError, QZoneNetworkError

__version__ = "1.0.1"

__all__ = [
    "QZoneClient",
    "QZoneConfig",
    "AuthProvider",
    "AuthInfo",
    "NapCatAuthProvider",
    "ManualCookieProvider",
    "CookieFileProvider",
    "QRCodeLoginProvider",
    "QZoneError",
    "QZoneAuthError",
    "QZoneApiError",
    "QZoneNetworkError",
    "__version__",
]
