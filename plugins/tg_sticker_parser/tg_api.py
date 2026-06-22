from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import httpx


TG_STICKER_RE = re.compile(
    r"https?://(?:t\.me|telegram\.me)/addstickers/([A-Za-z][A-Za-z0-9_]{0,63})",
    re.IGNORECASE,
)


def extract_sticker_set_names(text: str) -> list[str]:
    names: list[str] = []
    for match in TG_STICKER_RE.finditer(text):
        name = match.group(1)
        if name not in names:
            names.append(name)
    return names


class TelegramApiError(RuntimeError):
    pass


class TelegramBotApi:
    def __init__(self, token: str, api_base: str, proxy: str = "") -> None:
        if not token or "替换成" in token:
            raise TelegramApiError("Telegram bot_token 未配置")

        self.token = token
        self.api_base = api_base.rstrip("/")

        # 关键点：
        # trust_env=False 表示不读取 HTTP_PROXY / HTTPS_PROXY / ALL_PROXY 等环境变量。
        # 这样只使用 tg_sticker_parser.json 里的 proxy 配置，避免 systemd 环境代理污染。
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0),
            proxy=proxy or None,
            follow_redirects=True,
            trust_env=False,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def api_call(self, method: str, params: dict[str, Any]) -> Any:
        url = f"{self.api_base}/bot{self.token}/{method}"

        last_error: Any = None

        for _ in range(3):
            try:
                resp = await self.client.post(url, json=params)
                resp.raise_for_status()
                data = resp.json()
            except httpx.ConnectError as e:
                raise TelegramApiError(
                    f"连接 Telegram API 失败: {type(e).__name__}: {repr(e)}"
                ) from e
            except httpx.TimeoutException as e:
                raise TelegramApiError(
                    f"连接 Telegram API 超时: {type(e).__name__}: {repr(e)}"
                ) from e
            except httpx.HTTPError as e:
                raise TelegramApiError(
                    f"请求 Telegram API 失败: {type(e).__name__}: {repr(e)}"
                ) from e

            if data.get("ok"):
                return data.get("result")

            last_error = data
            retry_after = (data.get("parameters") or {}).get("retry_after")
            if retry_after:
                await asyncio.sleep(int(retry_after) + 1)
                continue

            raise TelegramApiError(f"Telegram API 错误: {data}")

        raise TelegramApiError(
            f"Telegram API 多次重试失败: method={method}, error={last_error}"
        )

    async def get_sticker_set(self, name: str) -> dict[str, Any]:
        return await self.api_call("getStickerSet", {"name": name})

    async def get_file(self, file_id: str) -> dict[str, Any]:
        return await self.api_call("getFile", {"file_id": file_id})

    async def download_file(self, file_path: str, save_path: Path) -> Path:
        url = f"{self.api_base}/file/bot{self.token}/{file_path}"
        save_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            async with self.client.stream("GET", url) as resp:
                resp.raise_for_status()
                with save_path.open("wb") as f:
                    async for chunk in resp.aiter_bytes():
                        if chunk:
                            f.write(chunk)
        except httpx.ConnectError as e:
            raise TelegramApiError(
                f"下载 Telegram 文件失败: {type(e).__name__}: {repr(e)}"
            ) from e
        except httpx.TimeoutException as e:
            raise TelegramApiError(
                f"下载 Telegram 文件超时: {type(e).__name__}: {repr(e)}"
            ) from e
        except httpx.HTTPError as e:
            raise TelegramApiError(
                f"下载 Telegram 文件请求失败: {type(e).__name__}: {repr(e)}"
            ) from e

        return save_path


def guess_extension(sticker: dict[str, Any], file_path: str) -> str:
    suffix = Path(file_path).suffix.lower()
    if suffix in {".webp", ".webm", ".tgs"}:
        return suffix

    if sticker.get("is_video"):
        return ".webm"

    if sticker.get("is_animated"):
        return ".tgs"

    return ".webp"