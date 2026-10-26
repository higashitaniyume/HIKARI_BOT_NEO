"""AI Agent 网络搜图与发图工具（`web_image_search` 与 `send_images`）测试。

覆盖：
- SearXNG 图片搜索解析（img_src, thumbnail_src, 绝对/相对路径归一）
- 会话级图片缓存与隔离
- 缓存外 URL 严格拦截（防 SSRF）
- send_images 安全下载、WebP/GIF 转码处理、调用 bot.send
- 发送张数上限限制
"""

from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch
from PIL import Image

from plugins.aiagent.tools import images as images_tool


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        data: dict[str, Any] | None = None,
        content: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._data = data or {}
        self._content = content
        self.headers = headers or {}
        self.text = content.decode("utf-8", errors="ignore") if content else ""

    def json(self) -> dict[str, Any]:
        return self._data

    async def aiter_bytes(self):
        yield self._content


class FakeStreamContext:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response

    async def __aenter__(self) -> FakeResponse:
        return self.response

    async def __aexit__(self, *exc: Any) -> None:
        return None


class FakeImageClient:
    gets: list[dict[str, Any]] = []
    get_response: FakeResponse | None = None
    stream_responses: dict[str, FakeResponse] = {}
    instances: list["FakeImageClient"] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.kwargs = kwargs
        FakeImageClient.instances.append(self)

    async def __aenter__(self) -> "FakeImageClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get(self, url: str, params: dict[str, Any] | None = None) -> FakeResponse:
        FakeImageClient.gets.append({"url": url, "params": params})
        if FakeImageClient.get_response is not None:
            return FakeImageClient.get_response
        return FakeResponse(200, {"results": []})

    def stream(self, method: str, url: str, **kwargs: Any) -> FakeStreamContext:
        resp = FakeImageClient.stream_responses.get(url)
        if resp is None:
            # 默认返回空 200
            resp = FakeResponse(200, content=b"")
        return FakeStreamContext(resp)


class FakeEvent:
    def __init__(self, user_id: int = 123456, group_id: int | None = None) -> None:
        self.user_id = user_id
        self.group_id = group_id

    def get_user_id(self) -> str:
        return str(self.user_id)


class FakeBot:
    def __init__(self) -> None:
        self.sent_messages: list[Any] = []

    async def send(self, event: Any, message: Any) -> None:
        self.sent_messages.append({"event": event, "message": message})


class FakeContext:
    def __init__(self, user_id: int = 123456, group_id: int | None = None) -> None:
        self.bot = FakeBot()
        self.event = FakeEvent(user_id=user_id, group_id=group_id)


def _cfg(*, allow_send: bool = True, **overrides: Any) -> dict[str, Any]:
    img_cfg = {
        "enabled": True,
        "allow_send": allow_send,
        "max_results": 5,
        "max_per_send": 3,
        "safesearch": 1,
        "max_bytes": 10485760,
        "download_timeout_seconds": 20,
    }
    img_cfg.update(overrides)
    # 单元测试默认关闭 Pixiv，避免真实联网；Pixiv 相关用例单独打开并 mock。
    img_cfg.setdefault("pixiv", {"enabled": False})
    return {
        "tools": {
            "search": {"base_url": "http://searxng-core:8080"},
            "images": img_cfg,
        }
    }


def _create_sample_png() -> bytes:
    bio = io.BytesIO()
    img = Image.new("RGBA", (100, 100), color=(255, 0, 0, 255))
    img.save(bio, format="PNG")
    return bio.getvalue()


def _create_sample_webp() -> bytes:
    bio = io.BytesIO()
    img = Image.new("RGB", (80, 80), color=(0, 255, 0))
    img.save(bio, format="WEBP")
    return bio.getvalue()


def _create_sample_gif() -> bytes:
    bio = io.BytesIO()
    img = Image.new("P", (60, 60))
    img.save(bio, format="GIF")
    return bio.getvalue()


class ImageSearchAndSendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        FakeImageClient.gets = []
        FakeImageClient.get_response = None
        FakeImageClient.stream_responses = {}
        FakeImageClient.instances = []
        images_tool.clear_session_cache()

    async def test_image_search_parses_results_and_caches_session(self) -> None:
        ctx = FakeContext(user_id=10001)
        FakeImageClient.get_response = FakeResponse(
            200,
            {
                "results": [
                    {
                        "title": "Cute Cat",
                        "url": "https://example.com/cat-page",
                        "img_src": "https://example.com/cat.jpg",
                        "thumbnail_src": "/image_proxy?url=https%3A%2F%2Fexample.com%2Fcat.jpg",
                        "resolution": "1920x1080",
                        "format": "jpeg",
                        "engine": "bing",
                    },
                    {
                        "title": "Relative Image Cat",
                        "url": "https://example.com/cat-page-2",
                        "img_src": "/image_proxy?url=https%3A%2F%2Fexample.com%2Fcat2.jpg",
                        "thumbnail_src": "",
                        "resolution": "800x600",
                        "format": "png",
                        "engine": "google",
                    },
                ]
            },
        )

        with patch.object(images_tool.httpx, "AsyncClient", FakeImageClient):
            output = await images_tool.execute(
                images_tool.WEB_IMAGE_SEARCH,
                _cfg(),
                {"query": "猫", "max_results": 2},
                ctx,
            )

        payload = json.loads(output)
        self.assertEqual(payload["query"], "猫")
        self.assertEqual(len(payload["results"]), 2)
        r0 = payload["results"][0]
        self.assertEqual(r0["title"], "Cute Cat")
        self.assertEqual(r0["image_url"], "https://example.com/cat.jpg")
        # 相对路径的 thumbnail_src 应补全 base_url
        self.assertEqual(
            r0["thumbnail_url"],
            "http://searxng-core:8080/image_proxy?url=https%3A%2F%2Fexample.com%2Fcat.jpg",
        )
        r1 = payload["results"][1]
        self.assertEqual(
            r1["image_url"],
            "http://searxng-core:8080/image_proxy?url=https%3A%2F%2Fexample.com%2Fcat2.jpg",
        )

        # 验证会话缓存已写入
        sid = images_tool.get_session_id(ctx)
        self.assertTrue(images_tool.is_url_in_session_cache(sid, "https://example.com/cat.jpg"))
        # 不同的会话不能读到
        self.assertFalse(images_tool.is_url_in_session_cache("private:99999", "https://example.com/cat.jpg"))

    async def test_send_images_rejects_uncached_url(self) -> None:
        ctx = FakeContext(user_id=10001)
        cfg = _cfg(allow_send=True)

        output = await images_tool.execute(
            images_tool.SEND_IMAGES,
            cfg,
            {"urls": ["https://evil.internal.local/secret.jpg"]},
            ctx,
        )
        payload = json.loads(output)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["sent"], 0)
        self.assertIn("not found in session image search cache", payload["failed"][0]["error"])

    async def test_send_images_success_downloads_and_converts(self) -> None:
        ctx = FakeContext(user_id=10001)
        cfg = _cfg(allow_send=True)
        sid = images_tool.get_session_id(ctx)

        png_url = "https://cdn.example.com/photo.png"
        webp_url = "https://cdn.example.com/graphic.webp"

        images_tool.record_session_images(
            sid,
            [{"image_url": png_url, "title": "PNG Photo"}, {"image_url": webp_url, "title": "WebP Graphic"}],
        )

        FakeImageClient.stream_responses[png_url] = FakeResponse(200, content=_create_sample_png())
        FakeImageClient.stream_responses[webp_url] = FakeResponse(200, content=_create_sample_webp())

        with (
            patch.object(images_tool.httpx, "AsyncClient", FakeImageClient),
            patch.object(images_tool, "_host_is_allowed", AsyncMock(return_value=True)),
        ):
            output = await images_tool.execute(
                images_tool.SEND_IMAGES,
                cfg,
                {"urls": [png_url, webp_url]},
                ctx,
            )

        payload = json.loads(output)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["sent"], 2)
        self.assertEqual(len(payload["paths"]), 2)

        # 检查 bot.send 被调用
        self.assertEqual(len(ctx.bot.sent_messages), 1)
        sent_msg = ctx.bot.sent_messages[0]["message"]
        self.assertEqual(len(sent_msg), 2)

    async def test_send_images_disabled_by_config(self) -> None:
        ctx = FakeContext(user_id=10001)
        cfg = _cfg(allow_send=False)

        output = await images_tool.execute(
            images_tool.SEND_IMAGES,
            cfg,
            {"urls": ["https://example.com/image.png"]},
            ctx,
        )
        payload = json.loads(output)
        self.assertIn("disabled by configuration", payload["error"])

    async def test_send_images_max_limit_enforced(self) -> None:
        ctx = FakeContext(user_id=10001)
        cfg = _cfg(allow_send=True, max_per_send=2)
        sid = images_tool.get_session_id(ctx)

        urls = [f"https://cdn.example.com/img{i}.png" for i in range(5)]
        images_tool.record_session_images(sid, [{"image_url": u} for u in urls])
        for u in urls:
            FakeImageClient.stream_responses[u] = FakeResponse(200, content=_create_sample_png())

        with (
            patch.object(images_tool.httpx, "AsyncClient", FakeImageClient),
            patch.object(images_tool, "_host_is_allowed", AsyncMock(return_value=True)),
        ):
            output = await images_tool.execute(
                images_tool.SEND_IMAGES,
                cfg,
                {"urls": urls, "max_images": 5},
                ctx,
            )

        payload = json.loads(output)
        self.assertTrue(payload["ok"])
        # 最多只能发送 2 张（受 max_per_send=2 限制）
        self.assertEqual(payload["sent"], 2)


class PixivPrioritySearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        FakeImageClient.gets = []
        FakeImageClient.get_response = None
        FakeImageClient.stream_responses = {}
        FakeImageClient.instances = []
        images_tool.clear_session_cache()

    @staticmethod
    def _pixiv_results(count: int) -> dict[str, Any]:
        return {
            "results": [
                {
                    "title": f"Pixiv {i}",
                    "page_url": f"https://www.pixiv.net/artworks/{1000 + i}",
                    "image_url": f"https://i.pximg.net/img-master/img/x/{1000 + i}_p0_master1200.jpg",
                    "thumbnail_url": f"https://i.pximg.net/c/250x250/{1000 + i}_square1200.jpg",
                    "resolution": "1200x1600",
                    "format": "jpg",
                    "engine": "pixiv",
                    "illust_id": str(1000 + i),
                    "artist": "artist",
                    "tags": ["百合"],
                }
                for i in range(count)
            ]
        }

    @staticmethod
    def _web_response(count: int) -> FakeResponse:
        return FakeResponse(
            200,
            {
                "results": [
                    {
                        "title": f"Web {i}",
                        "url": f"https://example.com/page{i}",
                        "img_src": f"https://example.com/img{i}.jpg",
                        "thumbnail_src": "",
                        "resolution": "800x600",
                        "format": "jpg",
                        "engine": "bing",
                    }
                    for i in range(count)
                ]
            },
        )

    async def test_pixiv_first_then_web_fills_remaining(self) -> None:
        ctx = FakeContext(user_id=10001)
        cfg = _cfg(pixiv={"enabled": True, "max_results": 2, "fallback_web": True})
        FakeImageClient.get_response = self._web_response(5)
        pixiv_search = AsyncMock(return_value=self._pixiv_results(2))

        with (
            patch.object(images_tool.pixiv_images, "search", pixiv_search),
            patch.object(images_tool.httpx, "AsyncClient", FakeImageClient),
        ):
            output = await images_tool.execute(
                images_tool.WEB_IMAGE_SEARCH,
                cfg,
                {"query": "百合", "max_results": 3},
                ctx,
            )

        payload = json.loads(output)
        self.assertEqual(payload["source"], "pixiv+web")
        self.assertEqual(payload["count"], 3)
        self.assertEqual(
            [item["engine"] for item in payload["results"]], ["pixiv", "pixiv", "bing"]
        )
        pixiv_search.assert_awaited_once()
        # Pixiv 只取配置上限的 2 张，剩 1 个名额交给网页搜索
        self.assertEqual(pixiv_search.await_args.args[2], 2)
        self.assertEqual(len(FakeImageClient.gets), 1)

        sid = images_tool.get_session_id(ctx)
        for item in payload["results"]:
            self.assertTrue(images_tool.is_url_in_session_cache(sid, item["image_url"]))

    async def test_pixiv_enough_results_skips_web_search(self) -> None:
        ctx = FakeContext(user_id=10001)
        cfg = _cfg(pixiv={"enabled": True, "max_results": 3, "fallback_web": True})
        FakeImageClient.get_response = self._web_response(3)

        with (
            patch.object(
                images_tool.pixiv_images,
                "search",
                AsyncMock(return_value=self._pixiv_results(3)),
            ),
            patch.object(images_tool.httpx, "AsyncClient", FakeImageClient),
        ):
            output = await images_tool.execute(
                images_tool.WEB_IMAGE_SEARCH,
                cfg,
                {"query": "百合", "max_results": 3},
                ctx,
            )

        payload = json.loads(output)
        self.assertEqual(payload["source"], "pixiv")
        self.assertEqual(payload["count"], 3)
        self.assertEqual(FakeImageClient.gets, [])

    async def test_source_web_skips_pixiv(self) -> None:
        ctx = FakeContext(user_id=10001)
        cfg = _cfg(pixiv={"enabled": True, "max_results": 3, "fallback_web": True})
        FakeImageClient.get_response = self._web_response(2)
        pixiv_search = AsyncMock(return_value=self._pixiv_results(3))

        with (
            patch.object(images_tool.pixiv_images, "search", pixiv_search),
            patch.object(images_tool.httpx, "AsyncClient", FakeImageClient),
        ):
            output = await images_tool.execute(
                images_tool.WEB_IMAGE_SEARCH,
                cfg,
                {"query": "猫的照片", "source": "web", "max_results": 2},
                ctx,
            )

        payload = json.loads(output)
        pixiv_search.assert_not_awaited()
        self.assertEqual(payload["source"], "web")
        self.assertEqual([item["engine"] for item in payload["results"]], ["bing", "bing"])

    async def test_source_pixiv_does_not_fall_back_to_web(self) -> None:
        ctx = FakeContext(user_id=10001)
        cfg = _cfg(pixiv={"enabled": True, "max_results": 3, "fallback_web": True})
        FakeImageClient.get_response = self._web_response(3)

        with (
            patch.object(
                images_tool.pixiv_images,
                "search",
                AsyncMock(return_value=self._pixiv_results(1)),
            ),
            patch.object(images_tool.httpx, "AsyncClient", FakeImageClient),
        ):
            output = await images_tool.execute(
                images_tool.WEB_IMAGE_SEARCH,
                cfg,
                {"query": "百合", "source": "pixiv", "max_results": 3},
                ctx,
            )

        payload = json.loads(output)
        self.assertEqual(payload["source"], "pixiv")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(FakeImageClient.gets, [])

    async def test_pixiv_error_falls_back_to_web(self) -> None:
        ctx = FakeContext(user_id=10001)
        cfg = _cfg(pixiv={"enabled": True, "max_results": 3, "fallback_web": True})
        FakeImageClient.get_response = self._web_response(2)

        with (
            patch.object(
                images_tool.pixiv_images,
                "search",
                AsyncMock(
                    return_value={"results": [], "error": "Pixiv 搜图失败: TimeoutException"}
                ),
            ),
            patch.object(images_tool.httpx, "AsyncClient", FakeImageClient),
        ):
            output = await images_tool.execute(
                images_tool.WEB_IMAGE_SEARCH,
                cfg,
                {"query": "百合", "max_results": 2},
                ctx,
            )

        payload = json.loads(output)
        self.assertTrue(payload["results"])
        self.assertEqual(payload["source"], "web")
        self.assertIn("pixiv", payload["errors"])

    async def test_fallback_web_disabled_returns_pixiv_only(self) -> None:
        ctx = FakeContext(user_id=10001)
        cfg = _cfg(pixiv={"enabled": True, "max_results": 3, "fallback_web": False})
        FakeImageClient.get_response = self._web_response(3)

        with (
            patch.object(
                images_tool.pixiv_images,
                "search",
                AsyncMock(return_value=self._pixiv_results(1)),
            ),
            patch.object(images_tool.httpx, "AsyncClient", FakeImageClient),
        ):
            output = await images_tool.execute(
                images_tool.WEB_IMAGE_SEARCH,
                cfg,
                {"query": "百合", "max_results": 3},
                ctx,
            )

        payload = json.loads(output)
        self.assertEqual(payload["source"], "pixiv")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(FakeImageClient.gets, [])

    async def test_send_pixiv_image_uses_referer(self) -> None:
        ctx = FakeContext(user_id=10001)
        cfg = _cfg(allow_send=True)
        url = "https://i.pximg.net/img-master/img/x/1234_p0_master1200.jpg"
        sid = images_tool.get_session_id(ctx)
        images_tool.record_session_images(sid, [{"image_url": url, "title": "Pixiv"}])
        FakeImageClient.stream_responses[url] = FakeResponse(200, content=_create_sample_png())

        with (
            patch.object(images_tool.httpx, "AsyncClient", FakeImageClient),
            patch.object(images_tool, "_host_is_allowed", AsyncMock(return_value=True)),
            patch.object(
                images_tool.pixiv_images,
                "download_options",
                return_value=({"referer": "https://www.pixiv.net/"}, None),
            ),
        ):
            output = await images_tool.execute(
                images_tool.SEND_IMAGES,
                cfg,
                {"urls": [url]},
                ctx,
            )

        payload = json.loads(output)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["sent"], 1)
        referers = [
            instance.kwargs.get("headers", {}).get("referer")
            for instance in FakeImageClient.instances
        ]
        self.assertIn("https://www.pixiv.net/", referers)


if __name__ == "__main__":
    unittest.main()
