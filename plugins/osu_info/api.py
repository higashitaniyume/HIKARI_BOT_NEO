from __future__ import annotations

import asyncio
import time
from typing import Any
from urllib.parse import quote

import httpx

from core.bot_identity import bot_user_agent

MODE_ALIASES = {
    "osu": "osu",
    "std": "osu",
    "standard": "osu",
    "taiko": "taiko",
    "鼓": "taiko",
    "fruits": "fruits",
    "fruit": "fruits",
    "ctb": "fruits",
    "catch": "fruits",
    "mania": "mania",
    "m": "mania",
}

MODE_LABELS = {
    "osu": "osu!",
    "taiko": "osu!taiko",
    "fruits": "osu!catch",
    "mania": "osu!mania",
}


class OsuApiError(RuntimeError):
    pass


class OsuAuthError(OsuApiError):
    pass


class OsuNotFoundError(OsuApiError):
    pass


class OsuApiClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.api_base = str(config.get("api_base") or "https://osu.ppy.sh/api/v2").rstrip("/")
        self.oauth_url = str(config.get("oauth_url") or "https://osu.ppy.sh/oauth/token")
        self.timeout = float(config.get("timeout") or 20)
        self.proxy = str(config.get("proxy") or "").strip() or None
        self._token: str | None = None
        self._expires_at = 0.0
        self._token_lock = asyncio.Lock()

    def _client_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "timeout": self.timeout,
            "headers": {
                "Accept": "application/json",
                "User-Agent": bot_user_agent("osu_info"),
            },
        }
        if self.proxy:
            kwargs["proxy"] = self.proxy
        return kwargs

    async def _ensure_token(self) -> str:
        client_id = str(self.config.get("client_id") or "").strip()
        client_secret = str(self.config.get("client_secret") or "").strip()
        if not client_id or not client_secret:
            raise OsuAuthError("osu! API client_id/client_secret 未配置")

        now = time.time()
        if self._token and now < self._expires_at - 60:
            return self._token

        async with self._token_lock:
            now = time.time()
            if self._token and now < self._expires_at - 60:
                return self._token

            try:
                async with httpx.AsyncClient(**self._client_kwargs()) as client:
                    response = await client.post(
                        self.oauth_url,
                        data={
                            "client_id": client_id,
                            "client_secret": client_secret,
                            "grant_type": "client_credentials",
                            "scope": "public",
                        },
                    )
            except httpx.RequestError as e:
                raise OsuAuthError(f"osu! OAuth 连接失败: {type(e).__name__}") from e

            if response.status_code >= 400:
                raise OsuAuthError(f"osu! OAuth 失败: HTTP {response.status_code}")

            data = response.json()
            token = data.get("access_token")
            if not token:
                raise OsuAuthError("osu! OAuth 响应中没有 access_token")

            self._token = str(token)
            self._expires_at = time.time() + int(data.get("expires_in") or 3600)
            return self._token

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        token = await self._ensure_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-api-version": "20240529",
        }
        url = f"{self.api_base}/{path.lstrip('/')}"
        try:
            async with httpx.AsyncClient(**self._client_kwargs()) as client:
                response = await client.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=headers,
                )
        except httpx.RequestError as e:
            raise OsuApiError(f"osu! API 连接失败: {type(e).__name__}") from e

        if response.status_code == 404:
            raise OsuNotFoundError("osu! 没有找到对应数据")
        if response.status_code == 401:
            self._token = None
            raise OsuAuthError("osu! API 鉴权失败，请检查客户端 ID/密钥")
        if response.status_code >= 400:
            raise OsuApiError(f"osu! API 请求失败: HTTP {response.status_code}")
        return response.json()

    async def get_user(self, identifier: str | int, mode: str) -> dict[str, Any]:
        user = str(identifier).strip()
        if not user:
            raise OsuApiError("缺少 osu! 用户名或 ID")
        if not user.isdigit() and not user.startswith("@"):
            user = f"@{user}"
        return await self.request("GET", f"/users/{quote(user, safe='@')}/{mode}")

    async def get_user_scores(
        self,
        user_id: int,
        mode: str,
        score_type: str,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        data = await self.request(
            "GET",
            f"/users/{user_id}/scores/{score_type}",
            params={
                "mode": mode,
                "limit": max(1, min(int(limit), 20)),
                "legacy_only": 0,
                "include_fails": 0,
            },
        )
        return data if isinstance(data, list) else []

    async def get_ranking(
        self,
        mode: str,
        *,
        country: str | None = None,
        variant: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"filter": "all"}
        if country:
            params["country"] = country.upper()
        if variant:
            params["variant"] = variant
        return await self.request("GET", f"/rankings/{mode}/global", params=params)

    async def get_beatmap(self, beatmap_id: int) -> dict[str, Any]:
        return await self.request("GET", f"/beatmaps/{beatmap_id}")

    async def search_beatmapsets(
        self,
        query: str,
        *,
        mode: str,
    ) -> dict[str, Any]:
        params = {"q": query, "m": mode}
        return await self.request("GET", "/beatmapsets/search", params=params)


def normalize_mode(value: str | None, default: str = "osu") -> str:
    raw = str(value or "").strip().casefold()
    return MODE_ALIASES.get(raw) or MODE_ALIASES.get(str(default).casefold(), "osu")


def split_mode_and_target(args: str, default_mode: str) -> tuple[str, str]:
    text = args.strip()
    if not text:
        return normalize_mode(None, default_mode), ""

    parts = text.split()
    if parts and parts[0].casefold() in MODE_ALIASES:
        return normalize_mode(parts[0], default_mode), " ".join(parts[1:]).strip()
    if len(parts) > 1 and parts[-1].casefold() in MODE_ALIASES:
        return normalize_mode(parts[-1], default_mode), " ".join(parts[:-1]).strip()
    return normalize_mode(None, default_mode), text


def mode_label(mode: str) -> str:
    return MODE_LABELS.get(mode, mode)
