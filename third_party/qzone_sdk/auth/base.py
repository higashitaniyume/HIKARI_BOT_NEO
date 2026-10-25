"""认证抽象基类。"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..errors import QZoneAuthError
from ..utils.gtk import compute_g_tk


@dataclass
class AuthInfo:
    """认证信息包。"""

    cookies: str
    g_tk: int
    uin: str
    skey: str = ""
    p_skey: str = ""


class AuthProvider:
    """认证提供者基类。子类实现 get_auth() 即可。"""

    async def get_auth(self) -> AuthInfo:
        raise NotImplementedError


def auth_from_cookies(cookies: str, uin: str) -> AuthInfo:
    """从 Cookie 字符串构建认证信息（自动计算 g_tk）。"""
    # 注意：必须锚定 Cookie 名起始位置，否则 "skey=" 会误匹配 "p_skey=" 子串
    m = re.search(r"(?:^|;\s*)p_skey=([^;]+)", cookies)
    if not m:
        raise QZoneAuthError("Cookie 缺少 p_skey，请确认已登录或重新获取凭证")
    p_skey = m.group(1)
    g_tk = compute_g_tk(p_skey)
    skey_m = re.search(r"(?:^|;\s*)skey=([^;]+)", cookies)
    skey = skey_m.group(1) if skey_m else ""
    return AuthInfo(cookies=cookies, g_tk=g_tk, uin=uin, skey=skey, p_skey=p_skey)
