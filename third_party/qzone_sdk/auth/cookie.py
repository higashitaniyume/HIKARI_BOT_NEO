"""手动 Cookie / Cookie 文件认证提供者。"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ..errors import QZoneAuthError
from .base import AuthInfo, AuthProvider, auth_from_cookies

_COOKIE_DIR_NAME = ".qzone_sdk"
_COOKIE_FILE_NAME = "cookies.json"


def default_cookie_file() -> str:
    """默认凭证文件路径：~/.qzone_sdk/cookies.json。"""
    return os.path.join(os.path.expanduser("~"), _COOKIE_DIR_NAME, _COOKIE_FILE_NAME)


def save_cookie_file(auth: AuthInfo, path: str | None = None) -> str:
    """将认证信息保存为 JSON 凭证文件，返回文件路径。

    注意：Cookie 等同于账号访问权限，请妥善保管（POSIX 下自动设为 600 权限）。
    """
    target = Path(path or default_cookie_file())
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "cookies": auth.cookies,
        "uin": auth.uin,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(target, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    if os.name == "posix":
        os.chmod(target, 0o600)
    return str(target)


class ManualCookieProvider(AuthProvider):
    """
    手动提供 Cookie 和 UIN。

    适用于已有 Cookie 的场景（如从浏览器 DevTools 抓取）。

    Args:
        cookies: 完整的 Cookie 字符串
        uin: QQ 号
    """

    def __init__(self, cookies: str, uin: str):
        self._cookies = cookies
        self._uin = uin

    async def get_auth(self) -> AuthInfo:
        return auth_from_cookies(self._cookies, self._uin)


class CookieFileProvider(AuthProvider):
    """
    从凭证文件读取认证信息（由 qzone login --save 或 save_cookie_file 生成）。

    Args:
        path: 凭证文件路径，默认 ~/.qzone_sdk/cookies.json
    """

    def __init__(self, path: str | os.PathLike | None = None):
        self.path = path or default_cookie_file()

    def _load(self) -> AuthInfo:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise QZoneAuthError(f"无法读取凭证文件 {self.path}: {e}")
        cookies = data.get("cookies", "")
        uin = str(data.get("uin", ""))
        if not cookies or not uin:
            raise QZoneAuthError(f"凭证文件 {self.path} 内容不完整，请重新执行 qzone login --save")
        return auth_from_cookies(cookies, uin)

    async def get_auth(self) -> AuthInfo:
        return self._load()
