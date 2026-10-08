"""AstralPartySpectator HTTP 客户端。

只使用文档中的公开无状态接口 `GET /query?id=<观战码>`：
<https://github.com/higashitaniyume/AstralPartySpectator> 的 docs/API.md。
每次查询都会让观战服务重新登录官方服务器，因此超时远大于普通接口。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .models import SpectatorSnapshot

logger = logging.getLogger("HikariBot.AstralPartySpectatorApi")


class SpectatorError(Exception):
    """观战查询失败基类。"""

    def __init__(self, message: str = "", *, server_error: int | None = None) -> None:
        super().__init__(message)
        self.server_error = server_error


class SpectatorNotAvailable(SpectatorError):
    """观战码无效、房间不可观战等由服务端明确拒绝的情况。"""

    def __init__(self, message: str = "", *, server_error: int | None = None) -> None:
        super().__init__(message, server_error=server_error)


class SpectatorTimeout(SpectatorError):
    """查询超时。"""


class SpectatorUnavailable(SpectatorError):
    """连接不可用、响应无法解析等。"""


# 官方错误码 -> 用户可读原因，取自 docs/API.md。
SERVER_ERROR_MESSAGES: dict[int, str] = {
    11001: "房间无法加入，请重试。",
    11079: "房间尚未开始。",
    11080: "房间已经结束。",
    11081: "观战码无效、已过期，或对应房间当前不可观战。",
    11082: "观战人数已满。",
    11083: "房间内有玩家离线，暂时无法观战。",
}


def server_error_message(server_error: int | None, fallback: str = "") -> str:
    """把官方错误码翻译成可读原因，未知错误码回落到服务端原文。"""
    if server_error is None:
        return fallback
    known = SERVER_ERROR_MESSAGES.get(server_error)
    if known:
        return known
    if fallback:
        return f"{fallback}（错误码 {server_error}）"
    return f"观战查询被官方服务拒绝（错误码 {server_error}）"


def normalize_watch_code(value: str) -> str:
    """校验并规范化观战码。"""
    code = str(value or "").strip()
    if not code:
        raise SpectatorError("观战码为空")
    if len(code) > 64:
        raise SpectatorError("观战码过长")
    if any(char.isprintable() is False for char in code):
        raise SpectatorError("观战码包含控制字符")
    return code


def _endpoint_url(endpoint: str) -> str:
    base = str(endpoint or "").strip().rstrip("/")
    if not base:
        raise SpectatorUnavailable("未配置观战服务地址")
    if "://" not in base:
        base = f"http://{base}"
    return f"{base}/query"


def _error_from_response(response: httpx.Response) -> SpectatorError:
    """把服务端的错误响应转换成用户可读异常。

    错误响应形如：
    {"error": "operation_failed", "category": "OfficialRpcException",
     "serverError": 11081, "serverMessage": "..."}
    """
    payload: dict[str, Any] = {}
    try:
        loaded = response.json()
        if isinstance(loaded, dict):
            payload = loaded
    except (json.JSONDecodeError, ValueError):
        payload = {}

    server_error = payload.get("serverError")
    try:
        server_error = int(server_error) if server_error is not None else None
    except (TypeError, ValueError):
        server_error = None

    fallback = str(payload.get("serverMessage") or "").strip()
    message = server_error_message(server_error, fallback)

    if response.status_code == 504:
        return SpectatorTimeout(message or "观战查询超时")
    # 400/409 是官方明确拒绝；503 只在带官方错误码时才算“观战码不可用”。
    if response.status_code in (400, 409) or (response.status_code == 503 and server_error is not None):
        return SpectatorNotAvailable(message, server_error=server_error)
    return SpectatorUnavailable(message or f"观战服务返回 HTTP {response.status_code}", server_error=server_error)


class SpectatorClient:
    """一次性观战查询客户端。"""

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    @property
    def _timeout(self) -> float:
        try:
            return max(1.0, float(self._config.get("timeout") or 90))
        except (TypeError, ValueError):
            return 90.0

    def _client_kwargs(self) -> dict[str, Any]:
        proxy = str(self._config.get("proxy") or "").strip()
        kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(self._timeout, connect=10.0),
            "follow_redirects": True,
            "verify": bool(self._config.get("verify_ssl", True)),
            "headers": {
                "User-Agent": str(
                    self._config.get("user_agent") or "HikariBot/1.0 AstralPartySpectatorQuery"
                ),
                "Accept": "application/json",
            },
        }
        if proxy:
            kwargs["proxy"] = proxy
        return kwargs

    async def query(self, watch_code: str) -> SpectatorSnapshot:
        """查询观战码并返回四名玩家的手牌快照。"""
        code = normalize_watch_code(watch_code)
        url = _endpoint_url(str(self._config.get("endpoint") or ""))

        try:
            async with httpx.AsyncClient(**self._client_kwargs()) as client:
                response = await client.get(url, params={"id": code})
        except httpx.TimeoutException as exc:
            raise SpectatorTimeout(f"观战查询超时（>{self._timeout:.0f}s）") from exc
        except httpx.HTTPError as exc:
            raise SpectatorUnavailable(f"无法连接观战服务：{type(exc).__name__}") from exc

        if response.status_code != 200:
            raise _error_from_response(response)

        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise SpectatorUnavailable("观战服务返回了无法解析的内容") from exc
        if not isinstance(payload, dict):
            raise SpectatorUnavailable("观战服务返回了意外结构")

        snapshot = SpectatorSnapshot.from_payload(payload)
        if not snapshot.players:
            raise SpectatorUnavailable(str(payload.get("reason") or "观战服务没有返回玩家数据"))
        logger.info(
            "[AstralParty] 查询完成 room=%s players=%d",
            snapshot.room_id or "-",
            len(snapshot.players),
        )
        return snapshot
