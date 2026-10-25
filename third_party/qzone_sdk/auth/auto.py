"""自动认证：按候选顺序尝试，NapCat 在线时优先。"""
from __future__ import annotations

import logging

from ..errors import QZoneAuthError
from .base import AuthProvider
from .napcat import NapCatAuthProvider

logger = logging.getLogger("qzone_sdk")


class AutoAuthProvider(AuthProvider):
    """
    按候选顺序依次尝试认证，直到成功。

    典型用途：配置了 QQ 号（QZONE_UIN）时 NapCat 在线则直接使用，
    离线则自动回退到凭证文件 / 扫码登录等其它方式。
    """

    def __init__(self, candidates: list[AuthProvider], preferred_uin: str = ""):
        if not candidates:
            raise ValueError("至少需要一个候选认证提供者")
        self.candidates = candidates
        self.preferred_uin = preferred_uin

    async def get_auth(self):
        last_error: Exception | None = None
        for provider in self.candidates:
            try:
                auth = await provider.get_auth()
            except QZoneAuthError as e:
                last_error = e
                logger.info("认证方式 %s 不可用: %s", type(provider).__name__, e)
                continue
            # 仅对 NapCat 校验 QQ 号一致性（凭证文件/手动 Cookie 属于显式指定）
            if (
                self.preferred_uin
                and isinstance(provider, NapCatAuthProvider)
                and auth.uin != self.preferred_uin
            ):
                last_error = QZoneAuthError(
                    f"NapCat 登录的 QQ（{auth.uin}）与配置的 QQ（{self.preferred_uin}）不一致"
                )
                logger.warning("%s", last_error)
                continue
            return auth
        raise QZoneAuthError(f"所有认证方式均不可用：{last_error}")
