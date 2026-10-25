"""SDK 配置。"""
from __future__ import annotations

from dataclasses import dataclass, field

from .auth.base import AuthProvider
from .auth.resolve import resolve_provider


@dataclass
class QZoneConfig:
    """
    SDK 配置。

    Attributes:
        auth_provider: 认证提供者。默认自动解析：环境变量 Cookie → 凭证文件 → NapCat。
        timeout: HTTP 请求超时秒数。
    """

    auth_provider: AuthProvider = field(default_factory=resolve_provider)
    timeout: int = 30
