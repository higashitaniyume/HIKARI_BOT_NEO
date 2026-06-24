from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from PIL import Image

import plugins.osu_info as osu_plugin
from plugins.osu_info import api as osu_api
from plugins.osu_info.config import get_config as get_osu_config
from plugins.osu_info import render as osu_render
from plugins.osu_info import storage as osu_storage


class FakeEvent:
    def __init__(self, user_id: str = "10001") -> None:
        self.user_id = user_id

    def get_user_id(self) -> str:
        return self.user_id


class FakeContext:
    def __init__(self, args: str = "", user_id: str = "10001") -> None:
        self.args = args
        self.event = FakeEvent(user_id)
        self.sent: list[object] = []

    async def send(self, message) -> None:
        self.sent.append(message)


class FakeOsuClient:
    def __init__(self) -> None:
        self.user = {
            "id": 123456789,
            "username": "SampleUser",
            "avatar_url": "",
            "join_date": "2022-06-10T00:00:00Z",
            "country": {"code": "CN"},
            "statistics": {
                "global_rank": 10028,
                "country_rank": 321,
                "pp": 8631.56,
                "hit_accuracy": 96.52,
                "play_count": 12345,
                "play_time": 654321,
                "maximum_combo": 2345,
                "level": {"current": 100, "progress": 42},
                "grade_counts": {"ssh": 1, "ss": 2, "sh": 3, "s": 4, "a": 5},
            },
        }
        self.score = {
            "rank": "S",
            "pp": 321.45,
            "accuracy": 0.9876,
            "score": 123456789,
            "max_combo": 1234,
            "created_at": "2026-06-24T00:00:00Z",
            "mods": [{"acronym": "HD"}],
            "beatmap": {"id": 11, "version": "Insane"},
            "beatmapset": {"artist": "Artist", "title": "Title"},
        }
        self.beatmap = {
            "id": 11,
            "url": "https://osu.ppy.sh/beatmaps/11",
            "version": "Hard",
            "difficulty_rating": 4.56,
            "bpm": 180,
            "total_length": 123,
            "max_combo": 999,
            "cs": 4,
            "ar": 9,
            "accuracy": 8,
            "drain": 6,
            "playcount": 1000,
            "passcount": 500,
            "beatmapset": {
                "artist": "Artist",
                "title": "Song",
                "status": "ranked",
                "covers": {},
            },
        }
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    async def get_user(self, *args, **kwargs):
        self.calls.append(("get_user", args, kwargs))
        return self.user

    async def get_user_scores(self, *args, **kwargs):
        self.calls.append(("get_user_scores", args, kwargs))
        return [self.score]

    async def get_ranking(self, *args, **kwargs):
        self.calls.append(("get_ranking", args, kwargs))
        return {"ranking": [self.user]}

    async def get_beatmap(self, *args, **kwargs):
        self.calls.append(("get_beatmap", args, kwargs))
        return self.beatmap

    async def search_beatmapsets(self, *args, **kwargs):
        self.calls.append(("search_beatmapsets", args, kwargs))
        return {
            "beatmapsets": [
                {
                    "id": 1,
                    "artist": "Artist",
                    "title": "Song",
                    "status": "ranked",
                    "beatmaps": [{"difficulty_rating": 3.2}],
                }
            ]
        }


class OsuParsingTests(unittest.TestCase):
    def test_mode_and_target_parsing(self) -> None:
        self.assertEqual(osu_api.normalize_mode("std"), "osu")
        self.assertEqual(osu_api.normalize_mode("ctb"), "fruits")
        self.assertEqual(osu_api.normalize_mode("unknown", "mania"), "mania")
        self.assertEqual(
            osu_api.split_mode_and_target("mania SampleUser", "osu"),
            ("mania", "SampleUser"),
        )
        self.assertEqual(
            osu_api.split_mode_and_target("SampleUser taiko", "osu"),
            ("taiko", "SampleUser"),
        )
        self.assertEqual(
            osu_api.split_mode_and_target("SampleUser", "osu"),
            ("osu", "SampleUser"),
        )

    def test_score_args_and_beatmap_id_parsing(self) -> None:
        self.assertEqual(
            osu_plugin._score_args("recent mania SampleUser"),
            ("recent", "mania SampleUser"),
        )
        self.assertEqual(osu_plugin._score_args("bp SampleUser"), ("best", "SampleUser"))
        self.assertEqual(osu_plugin._extract_beatmap_id("https://osu.ppy.sh/beatmapsets/1#osu/234"), 234)
        self.assertEqual(osu_plugin._extract_beatmap_id("https://osu.ppy.sh/beatmaps/345"), 345)
        self.assertIsNone(osu_plugin._extract_beatmap_id("artist title"))


class OsuStorageTests(unittest.TestCase):
    def test_binding_lifecycle_uses_user_data_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "osu_bindings.json"
            with patch.object(osu_storage, "BINDINGS_PATH", path):
                self.assertIsNone(osu_storage.get_binding("42"))
                binding = osu_storage.set_binding("42", osu_id=7, username="player", mode="mania")
                self.assertEqual(binding.osu_id, 7)
                self.assertEqual(binding.mode, "mania")
                loaded = osu_storage.get_binding("42")
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded.username, "player")
                self.assertTrue(osu_storage.remove_binding("42"))
                self.assertFalse(osu_storage.remove_binding("42"))
                self.assertIsNone(osu_storage.get_binding("42"))


class FakeResponse:
    def __init__(self, status_code: int, data: dict[str, object]) -> None:
        self.status_code = status_code
        self._data = data

    def json(self) -> dict[str, object]:
        return self._data


class FakeAsyncClient:
    def __init__(self, *args, **kwargs) -> None:
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, *args, **kwargs):
        return FakeResponse(200, {"access_token": "token", "expires_in": 3600})

    async def request(self, *args, **kwargs):
        return {"ok": True}


class FailingAsyncClient(FakeAsyncClient):
    async def request(self, *args, **kwargs):
        request = httpx.Request("GET", "https://osu.ppy.sh/api/v2/users/1/osu")
        raise httpx.ConnectError("boom", request=request)


class OsuApiClientTests(unittest.IsolatedAsyncioTestCase):
    def configured_client(self) -> osu_api.OsuApiClient:
        cfg = get_osu_config()
        self.assertTrue(str(cfg.get("client_id") or "").strip())
        self.assertTrue(str(cfg.get("client_secret") or "").strip())
        return osu_api.OsuApiClient(cfg)

    async def test_missing_credentials_raise_auth_error(self) -> None:
        client = osu_api.OsuApiClient({"client_id": "", "client_secret": ""})
        with self.assertRaises(osu_api.OsuAuthError):
            await client._ensure_token()

    async def test_request_errors_are_wrapped(self) -> None:
        client = self.configured_client()
        client._token = "token"
        client._expires_at = time.time() + 3600
        with patch.object(osu_api.httpx, "AsyncClient", FailingAsyncClient):
            with self.assertRaisesRegex(osu_api.OsuApiError, "连接失败"):
                await client.request("GET", "/users/1/osu")


class OsuRenderTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_renderers_create_nonempty_images(self) -> None:
        client = FakeOsuClient()
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            with (
                patch.object(osu_render, "fetch_avatar", AsyncMock(return_value=None)),
                patch.object(osu_render, "fetch_image", AsyncMock(return_value=None)),
            ):
                paths = [
                    await osu_render.render_notice("标题", ["第一行", "第二行"], cache_dir),
                    await osu_render.render_user_card(client.user, "mania", cache_dir),
                    await osu_render.render_dashboard(client.user, [client.score] * 5, "mania", cache_dir),
                    await osu_render.render_scores(client.user, [client.score], "mania", "best", cache_dir),
                    await osu_render.render_ranking({"ranking": [client.user]}, "mania", cache_dir, limit=1),
                    await osu_render.render_beatmap(client.beatmap, cache_dir),
                    await osu_render.render_beatmap_search(
                        {
                            "beatmapsets": [
                                {
                                    "id": 1,
                                    "artist": "Artist",
                                    "title": "Title",
                                    "status": "ranked",
                                    "beatmaps": [{"difficulty_rating": 2.5}],
                                }
                            ]
                        },
                        "artist title",
                        "osu",
                        cache_dir,
                    ),
                ]
            for path in paths:
                self.assertTrue(path.exists(), path)
                self.assertGreater(path.stat().st_size, 0)
                with Image.open(path) as image:
                    self.assertGreater(image.size[0], 0)
                    self.assertGreater(image.size[1], 0)


class OsuCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.client = FakeOsuClient()
        self.config = {
            "enabled": True,
            "default_mode": "osu",
            "cache_dir": "/tmp/hikari_bot/osu_info_test",
            "score_limit": 5,
            "ranking_limit": 10,
            "beatmap_search_limit": 5,
            "proxy": "",
        }

    @contextmanager
    def patch_common(self):
        with (
            patch.object(osu_plugin, "get_config", Mock(return_value=self.config)),
            patch.object(osu_plugin, "_get_client", Mock(return_value=self.client)),
            patch.object(osu_plugin, "_send_image", AsyncMock()),
            patch.object(osu_plugin, "_send_notice", AsyncMock()),
            patch.object(osu_plugin, "stats_increment", Mock()),
        ):
            yield

    async def test_help_and_unbind_commands_send_notices(self) -> None:
        ctx = FakeContext()
        with patch.object(osu_plugin, "_send_notice", AsyncMock()) as notice:
            await osu_plugin.handle_osu_help(ctx)
            notice.assert_awaited_once()

        with patch.object(osu_plugin, "remove_binding", Mock(return_value=True)), patch.object(
            osu_plugin, "_send_notice", AsyncMock()
        ) as notice:
            await osu_plugin.handle_osu_unbind(ctx)
            notice.assert_awaited_once()

    async def test_bind_command_fetches_user_and_saves_binding(self) -> None:
        ctx = FakeContext("mania SampleUser")
        with (
            patch.object(osu_plugin, "get_config", Mock(return_value=self.config)),
            patch.object(osu_plugin, "_get_client", Mock(return_value=self.client)),
            patch.object(osu_plugin, "set_binding", Mock()),
            patch.object(osu_plugin, "render_user_card", AsyncMock(return_value=Path(__file__))),
            patch.object(osu_plugin, "_send_image", AsyncMock()) as send_image,
        ):
            await osu_plugin.handle_osu_bind(ctx)

        self.assertEqual(self.client.calls[0][0], "get_user")
        self.assertEqual(self.client.calls[0][1], ("SampleUser", "mania"))
        send_image.assert_awaited_once()

    async def test_user_dashboard_scores_ranking_commands(self) -> None:
        with self.patch_common():
            with patch.object(
                osu_plugin,
                "_get_bound_or_named_user",
                AsyncMock(return_value=(self.client.user, "mania")),
            ):
                with patch.object(osu_plugin, "render_user_card", AsyncMock(return_value=Path(__file__))):
                    await osu_plugin.handle_osu_user(FakeContext(""))
                with patch.object(osu_plugin, "render_dashboard", AsyncMock(return_value=Path(__file__))):
                    await osu_plugin.handle_osu_dashboard(FakeContext("mania SampleUser"))
                with patch.object(osu_plugin, "render_scores", AsyncMock(return_value=Path(__file__))):
                    await osu_plugin.handle_osu_scores(FakeContext("recent mania SampleUser"))

            with patch.object(osu_plugin, "render_ranking", AsyncMock(return_value=Path(__file__))):
                await osu_plugin.handle_osu_ranking(FakeContext("mania 4k CN"))

        score_calls = [call for call in self.client.calls if call[0] == "get_user_scores"]
        self.assertEqual(score_calls[0][1], (123456789, "mania", "recent"))
        self.assertIn(("get_ranking", ("mania",), {"country": "CN", "variant": "4k"}), self.client.calls)

    async def test_beatmap_command_handles_id_and_search(self) -> None:
        with self.patch_common():
            with patch.object(osu_plugin, "render_beatmap", AsyncMock(return_value=Path(__file__))):
                await osu_plugin.handle_osu_beatmap(FakeContext("https://osu.ppy.sh/beatmaps/11"))
            with patch.object(osu_plugin, "render_beatmap_search", AsyncMock(return_value=Path(__file__))):
                await osu_plugin.handle_osu_beatmap(FakeContext("mania camellia"))

        self.assertIn(("get_beatmap", (11,), {}), self.client.calls)
        self.assertIn(("search_beatmapsets", ("camellia",), {"mode": "mania"}), self.client.calls)

    async def test_missing_beatmap_argument_sends_notice(self) -> None:
        with patch.object(osu_plugin, "_send_notice", AsyncMock()) as notice:
            await osu_plugin.handle_osu_beatmap(FakeContext(""))
            notice.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
