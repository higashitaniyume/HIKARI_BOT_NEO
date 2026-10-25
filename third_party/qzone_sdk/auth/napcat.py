"""NapCat WebSocket 认证提供者。"""
from __future__ import annotations

import asyncio
import json
import re
import uuid

from ..errors import QZoneAuthError, QZoneNetworkError
from .base import AuthInfo, AuthProvider, auth_from_cookies


class NapCatAuthProvider(AuthProvider):
    """
    通过 NapCat WebSocket 接口获取认证信息。

    NapCat 是 QQ 的第三方协议实现，提供 WebSocket API。
    本提供者通过 get_cookies 和 get_login_info 两个 action
    获取当前登录 QQ 的 Cookie 和 UIN。

    Args:
        ws_url: NapCat WebSocket 地址，默认 ws://localhost:3001
        timeout: WebSocket 超时秒数
    """

    def __init__(self, ws_url: str = "ws://localhost:3001", timeout: float = 10):
        self.ws_url = ws_url
        self.timeout = timeout

    async def _ws_action(self, action: str, params: dict) -> dict:
        """单次 WebSocket 请求-响应。"""
        try:
            import websockets
        except ImportError:
            raise QZoneAuthError(
                "NapCat 模式需要 websockets 库：pip install 'qzone-sdk[napcat]'"
            )

        echo = str(uuid.uuid4())
        try:
            async with websockets.connect(
                self.ws_url, max_size=10 * 1024 * 1024, open_timeout=self.timeout
            ) as ws:
                await ws.send(json.dumps({"action": action, "params": params, "echo": echo}))
                for _ in range(50):
                    raw = await asyncio.wait_for(ws.recv(), timeout=self.timeout)
                    data = json.loads(raw)
                    if data.get("echo") == echo:
                        if data.get("status") != "ok":
                            raise QZoneAuthError(
                                f"NapCat error: {data.get('message', 'unknown')}"
                            )
                        return data["data"]
        except (OSError, TimeoutError) as e:
            raise QZoneAuthError(
                f"无法连接 NapCat ({self.ws_url}): {e}。"
                "若未运行 NapCat，可改用扫码登录：先执行 `qzone login --save`，"
                "或设置 QZONE_COOKIE/QZONE_UIN 环境变量，"
                "或在代码中使用 QRCodeLoginProvider()/ManualCookieProvider()。"
            ) from e
        raise QZoneNetworkError("NapCat 响应超时")

    async def get_auth(self) -> AuthInfo:
        cookie_data = await self._ws_action(
            "get_cookies", {"domain": "user.qzone.qq.com"}
        )
        cookies = cookie_data["cookies"]

        if not re.search(r"(?:^|;\s*)p_skey=", cookies):
            raise QZoneAuthError("Cookie 缺少 p_skey，请确认 QQ 已登录")

        login_data = await self._ws_action("get_login_info", {})
        uin = str(login_data["user_id"])

        return auth_from_cookies(cookies, uin)
