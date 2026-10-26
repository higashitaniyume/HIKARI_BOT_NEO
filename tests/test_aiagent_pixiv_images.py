"""Pixiv 优先搜图数据源（`plugins/aiagent/tools/pixiv_images.py`）测试。"""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import patch

from plugins.aiagent.tools import pixiv_images


class FakePixivResponse:
    def __init__(self, status_code: int, data: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._data = data or {}
        self.text = json.dumps(self._data, ensure_ascii=False)[:300]

    def json(self) -> dict[str, Any]:
        return self._data


class FakePixivClient:
    search_items_by_mode: dict[str, list[dict[str, Any]]] = {}
    pages_by_id: dict[str, dict[str, Any]] = {}
    search_status = 200
    pages_status = 200
    response_key = "illustManga"
    pages_raise_ids: set[str] = set()
    calls: list[tuple[str, dict[str, Any] | None]] = []
    instances: list["FakePixivClient"] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.kwargs = kwargs
        FakePixivClient.instances.append(self)

    async def __aenter__(self) -> "FakePixivClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get(self, url: str, params: dict[str, Any] | None = None) -> FakePixivResponse:
        FakePixivClient.calls.append((url, params))
        if "/ajax/search/artworks/" in url:
            mode = str((params or {}).get("s_mode") or "")
            items = FakePixivClient.search_items_by_mode.get(mode, [])
            body = {FakePixivClient.response_key: {"data": items}}
            return FakePixivResponse(
                FakePixivClient.search_status, {"error": False, "body": body}
            )
        if url.endswith("/pages"):
            illust_id = url.split("/ajax/illust/")[1].split("/")[0]
            if illust_id in FakePixivClient.pages_raise_ids:
                raise RuntimeError("pages boom")
            urls = FakePixivClient.pages_by_id.get(illust_id, {})
            return FakePixivResponse(
                FakePixivClient.pages_status,
                {"error": False, "body": [{"urls": urls}]},
            )
        return FakePixivResponse(404, {})


def _item(
    illust_id: int,
    *,
    x_restrict: int = 0,
    illust_type: int = 0,
    page_count: int = 1,
    title: str = "",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": str(illust_id),
        "title": title or f"work {illust_id}",
        "xRestrict": x_restrict,
        "illustType": illust_type,
        "pageCount": page_count,
        "width": 1200,
        "height": 1600,
        "url": f"https://i.pximg.net/c/250x250/img/{illust_id}_square1200.jpg",
        "userName": "artist",
        "tags": tags if tags is not None else ["百合", "girl"],
    }


def _urls(illust_id: int) -> dict[str, Any]:
    return {
        "regular": f"https://i.pximg.net/img-master/img/{illust_id}_p0_master1200.jpg",
        "small": f"https://i.pximg.net/c/540x540_70/img-master/img/{illust_id}_p0.jpg",
        "original": f"https://i.pximg.net/img-original/img/{illust_id}_p0.png",
    }


def _cfg(**pixiv_overrides: Any) -> dict[str, Any]:
    options = {"enabled": True, "max_results": 3, "fallback_web": True, "order": "date_d"}
    options.update(pixiv_overrides)
    return {"tools": {"images": {"pixiv": options}}}


PLUGIN_CONFIG = {"cookie": "PHPSESSID=test", "allow_r18": False, "proxy": ""}


class PixivSearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        FakePixivClient.search_items_by_mode = {}
        FakePixivClient.pages_by_id = {}
        FakePixivClient.search_status = 200
        FakePixivClient.pages_status = 200
        FakePixivClient.response_key = "illustManga"
        FakePixivClient.pages_raise_ids = set()
        FakePixivClient.calls = []
        FakePixivClient.instances = []

    def _patch_client(self, plugin_config: dict[str, Any] | None = None):
        config = PLUGIN_CONFIG if plugin_config is None else plugin_config
        return (
            patch.object(pixiv_images.httpx, "AsyncClient", FakePixivClient),
            patch.object(pixiv_images, "load_plugin_config", return_value=config),
        )

    async def _search(self, cfg: dict[str, Any], limit: int = 3, safesearch: int = 1):
        patch_client, patch_config = self._patch_client()
        with patch_client, patch_config:
            return await pixiv_images.search(cfg, "百合", limit, safesearch)

    async def test_parses_illustmanga_and_resolves_regular_url(self) -> None:
        FakePixivClient.search_items_by_mode = {
            "s_tag_full": [_item(1001), _item(1002), _item(1003)]
        }
        FakePixivClient.pages_by_id = {
            "1001": _urls(1001),
            "1002": _urls(1002),
            "1003": _urls(1003),
        }

        payload = await self._search(_cfg(), limit=2)

        results = payload["results"]
        self.assertEqual(len(results), 2)
        first = results[0]
        self.assertEqual(first["engine"], "pixiv")
        self.assertEqual(first["illust_id"], "1001")
        self.assertEqual(first["image_url"], _urls(1001)["regular"])
        self.assertEqual(first["page_url"], "https://www.pixiv.net/artworks/1001")
        self.assertEqual(first["resolution"], "1200x1600")
        self.assertEqual(first["format"], "jpg")
        self.assertEqual(first["artist"], "artist")
        self.assertEqual(first["tags"], ["百合", "girl"])
        # 候选作品会并发取 pages；结果只保留前 limit 张
        self.assertEqual(
            len([url for url, _ in FakePixivClient.calls if url.endswith("/pages")]), 3
        )

    async def test_supports_legacy_illust_body(self) -> None:
        FakePixivClient.response_key = "illust"
        FakePixivClient.search_items_by_mode = {"s_tag_full": [_item(2001)]}
        FakePixivClient.pages_by_id = {"2001": _urls(2001)}

        payload = await self._search(_cfg(), limit=1)
        self.assertEqual(len(payload["results"]), 1)
        self.assertEqual(payload["results"][0]["illust_id"], "2001")

    async def test_falls_back_to_partial_tag_search(self) -> None:
        FakePixivClient.search_items_by_mode = {
            "s_tag_full": [],
            "s_tag": [_item(3001)],
        }
        FakePixivClient.pages_by_id = {"3001": _urls(3001)}

        payload = await self._search(_cfg(), limit=1)

        self.assertEqual(len(payload["results"]), 1)
        modes = [str((params or {}).get("s_mode")) for _, params in FakePixivClient.calls]
        self.assertEqual(modes[:2], ["s_tag_full", "s_tag"])

    async def test_filters_r18_by_default(self) -> None:
        FakePixivClient.search_items_by_mode = {
            "s_tag_full": [_item(4001, x_restrict=1), _item(4002)]
        }
        FakePixivClient.pages_by_id = {"4001": _urls(4001), "4002": _urls(4002)}

        payload = await self._search(_cfg(), limit=3)

        self.assertEqual([item["illust_id"] for item in payload["results"]], ["4002"])
        pages_calls = [url for url, _ in FakePixivClient.calls if url.endswith("/pages")]
        self.assertEqual(pages_calls, ["https://www.pixiv.net/ajax/illust/4002/pages"])

    async def test_allows_r18_only_when_plugin_and_safesearch_allow(self) -> None:
        FakePixivClient.search_items_by_mode = {
            "s_tag_full": [_item(4001, x_restrict=1), _item(4002)]
        }
        FakePixivClient.pages_by_id = {"4001": _urls(4001), "4002": _urls(4002)}

        patch_client, patch_config = self._patch_client(
            {**PLUGIN_CONFIG, "allow_r18": True}
        )
        with patch_client, patch_config:
            payload = await pixiv_images.search(_cfg(), "百合", 3, safesearch=0)

        self.assertEqual(
            [item["illust_id"] for item in payload["results"]], ["4001", "4002"]
        )

    async def test_without_cookie_skips_pixiv(self) -> None:
        patch_client, patch_config = self._patch_client({})
        with patch_client, patch_config:
            payload = await pixiv_images.search(_cfg(), "百合", 3)

        self.assertEqual(payload["results"], [])
        self.assertIn("Cookie", payload["note"])
        self.assertEqual(FakePixivClient.calls, [])

    async def test_pages_failure_is_skipped(self) -> None:
        FakePixivClient.search_items_by_mode = {
            "s_tag_full": [_item(5001), _item(5002)]
        }
        FakePixivClient.pages_by_id = {"5002": _urls(5002)}
        FakePixivClient.pages_raise_ids = {"5001"}

        payload = await self._search(_cfg(), limit=2)

        self.assertEqual([item["illust_id"] for item in payload["results"]], ["5002"])

    async def test_search_http_error_returns_empty(self) -> None:
        FakePixivClient.search_status = 403

        payload = await self._search(_cfg(), limit=3)

        self.assertEqual(payload["results"], [])
        self.assertIn("没有找到", payload["note"])

    async def test_skips_ugoira_and_empty_page_count(self) -> None:
        FakePixivClient.search_items_by_mode = {
            "s_tag_full": [
                _item(6001, illust_type=2),
                _item(6002, page_count=0),
                _item(6003),
            ]
        }
        FakePixivClient.pages_by_id = {"6003": _urls(6003)}

        payload = await self._search(_cfg(), limit=3)

        self.assertEqual([item["illust_id"] for item in payload["results"]], ["6003"])


class PixivHelperTests(unittest.TestCase):
    def test_is_pixiv_image_url(self) -> None:
        self.assertTrue(
            pixiv_images.is_pixiv_image_url(
                "https://i.pximg.net/img-master/img/123_p0_master1200.jpg"
            )
        )
        self.assertFalse(
            pixiv_images.is_pixiv_image_url("https://example.com/123.jpg")
        )
        self.assertFalse(pixiv_images.is_pixiv_image_url(""))

    def test_download_options_include_referer_and_cookie(self) -> None:
        with patch.object(
            pixiv_images,
            "load_plugin_config",
            return_value={"cookie": "PHPSESSID=abc", "proxy": ""},
        ):
            headers, proxy = pixiv_images.download_options()
        self.assertEqual(headers["referer"], "https://www.pixiv.net/")
        self.assertEqual(headers["cookie"], "PHPSESSID=abc")
        self.assertIsNone(proxy)

    def test_config_clamps(self) -> None:
        cfg = _cfg(max_results=99, order="popular_d", timeout_seconds=999)
        self.assertEqual(pixiv_images.max_results(cfg), 10)
        self.assertEqual(pixiv_images.order(cfg), "date_d")
        self.assertEqual(pixiv_images.timeout_seconds(cfg), 60)
        self.assertTrue(pixiv_images.fallback_web(_cfg()))


if __name__ == "__main__":
    unittest.main()
