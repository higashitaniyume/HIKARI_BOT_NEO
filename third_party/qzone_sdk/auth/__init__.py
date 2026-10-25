"""认证提供者。"""
from .auto import AutoAuthProvider
from .base import AuthInfo, AuthProvider, auth_from_cookies
from .cookie import CookieFileProvider, ManualCookieProvider, default_cookie_file, save_cookie_file
from .napcat import NapCatAuthProvider
from .qrcode import QRCodeLoginProvider
from .resolve import resolve_provider

__all__ = [
    "AuthInfo",
    "AuthProvider",
    "auth_from_cookies",
    "ManualCookieProvider",
    "CookieFileProvider",
    "default_cookie_file",
    "save_cookie_file",
    "NapCatAuthProvider",
    "QRCodeLoginProvider",
    "AutoAuthProvider",
    "resolve_provider",
]
