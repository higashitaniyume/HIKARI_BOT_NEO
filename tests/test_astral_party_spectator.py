from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

import plugins.astral_party_spectator as spectator_plugin
from core.command_router import iter_commands
from plugins.astral_party_spectator import api as spectator_api
from plugins.astral_party_spectator.api import (
    SpectatorClient,
    SpectatorNotAvailable,
    SpectatorTimeout,
    SpectatorUnavailable,
    normalize_watch_code,
    server_error_message,
)
from plugins.astral_party_spectator.formatting import format_hand_message
from plugins.astral_party_spectator.models import SpectatorSnapshot

SAMPLE_PAYLOAD: dict[str, object] = {
    "mode": "pve",
    "officialSpectating": True,
    "status": "ready",
    "reason": None,
    "roomId": "943222",
    "playerId": "1744079",
    "handCount": 6,
    "cards": [],
    "players": [
        {
            "playerId": "1744079",
            "slot": 0,
            "nick": "企鹅鹅大魔王",
            "heroId": 102,
            "hp": 12,
            "maxHp": 12,
            "gold": 27,
            "attack": 1,
            "defense": 2,
            "handKnown": True,
            "handCount": 6,
            "handReason": "authoritative",
            "cards": [
                {
                    "cardUid": 1,
                    "cardId": 20006,
                    "purifyNum": 0,
                    "isTemp": False,
                    "battleCost": 0,
                    "name": "以毒攻毒",
                    "description": "自身立刻失去2点生命\n回合开始恢复3点生命值，持续2回合",
                    "baseCost": 0,
                },
                {
                    "cardUid": 11,
                    "cardId": 10002,
                    "purifyNum": 2,
                    "isTemp": False,
                    "battleCost": 1,
                    "name": "防御(中)",
                    "description": "战斗中防御方可用",
                    "baseCost": 1,
                },
            ],
            "heroName": "芬妮",
        },
        {
            "playerId": "2948086",
            "slot": 2,
            "nick": "幻榊陽",
            "heroId": 108,
            "hp": 2,
            "maxHp": 11,
            "gold": 14,
            "attack": 2,
            "defense": 2,
            "handKnown": False,
            "handCount": 8,
            "handReason": "server_masked_or_incomplete",
            "cards": [],
            "heroName": "米米",
        },
    ],
}


class FakeContext:
    def __init__(self, args: str = "") -> None:
        self.args = args
        self.sent: list[object] = []

    async def send(self, message) -> None:
        self.sent.append(message)


class FakeResponse:
    def __init__(self, status_code: int, data: object) -> None:
        self.status_code = status_code
        self._data = data

    def json(self) -> object:
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


class FakeAsyncClient:
    next_response: FakeResponse | None = None
    next_error: Exception | None = None
    last_url: str = ""
    last_params: dict[str, object] | None = None
    last_kwargs: dict[str, object] = {}

    def __init__(self, *args, **kwargs) -> None:
        FakeAsyncClient.last_kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url, params=None):
        FakeAsyncClient.last_url = str(url)
        FakeAsyncClient.last_params = dict(params or {})
        if FakeAsyncClient.next_error is not None:
            raise FakeAsyncClient.next_error
        assert FakeAsyncClient.next_response is not None
        return FakeAsyncClient.next_response


def configured_client(**overrides) -> SpectatorClient:
    config = {
        "endpoint": "http://192.168.31.2:18743",
        "timeout": 30,
        "verify_ssl": True,
        "proxy": "",
        "user_agent": "test-agent",
    }
    config.update(overrides)
    return SpectatorClient(config)


class SpectatorApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        FakeAsyncClient.next_response = None
        FakeAsyncClient.next_error = None
        FakeAsyncClient.last_url = ""
        FakeAsyncClient.last_params = None
        FakeAsyncClient.last_kwargs = {}

    @patch.object(spectator_api.httpx, "AsyncClient", FakeAsyncClient)
    async def test_query_parses_players_and_cards(self) -> None:
        FakeAsyncClient.next_response = FakeResponse(200, json.loads(json.dumps(SAMPLE_PAYLOAD)))

        snapshot = await configured_client().query(" C27ED36 ")

        self.assertEqual(FakeAsyncClient.last_url, "http://192.168.31.2:18743/query")
        self.assertEqual(FakeAsyncClient.last_params, {"id": "C27ED36"})
        self.assertEqual(snapshot.room_id, "943222")
        self.assertEqual([player.slot for player in snapshot.players], [0, 2])

        first = snapshot.players[0]
        self.assertEqual(first.nick, "企鹅鹅大魔王")
        self.assertEqual(first.hero_name, "芬妮")
        self.assertEqual((first.hp, first.max_hp, first.gold, first.attack, first.defense), (12, 12, 27, 1, 2))
        self.assertTrue(first.hand_known)
        self.assertEqual(len(first.cards), 2)
        self.assertEqual(first.cards[0].name, "以毒攻毒")
        self.assertEqual(first.cards[1].battle_cost, 1)
        self.assertEqual(first.cards[1].purify_num, 2)

    @patch.object(spectator_api.httpx, "AsyncClient", FakeAsyncClient)
    async def test_masked_hand_drops_cards(self) -> None:
        FakeAsyncClient.next_response = FakeResponse(200, json.loads(json.dumps(SAMPLE_PAYLOAD)))

        snapshot = await configured_client().query("C27ED36")

        masked = snapshot.players[1]
        self.assertFalse(masked.hand_known)
        self.assertEqual(masked.hand_count, 8)
        self.assertEqual(masked.cards, [])

    @patch.object(spectator_api.httpx, "AsyncClient", FakeAsyncClient)
    async def test_negative_battle_cost_falls_back_to_base_cost(self) -> None:
        payload = json.loads(json.dumps(SAMPLE_PAYLOAD))
        payload["players"][0]["cards"][0]["battleCost"] = -1
        FakeAsyncClient.next_response = FakeResponse(200, payload)

        snapshot = await configured_client().query("C27ED36")

        self.assertEqual(snapshot.players[0].cards[0].battle_cost, 0)

    @patch.object(spectator_api.httpx, "AsyncClient", FakeAsyncClient)
    async def test_official_rejection_maps_to_not_available(self) -> None:
        FakeAsyncClient.next_response = FakeResponse(
            409,
            {
                "error": "operation_failed",
                "category": "OfficialRpcException",
                "serverError": 11081,
                "serverMessage": "观战码无效、已过期，或对应房间当前不可观战。",
            },
        )

        with self.assertRaises(SpectatorNotAvailable) as caught:
            await configured_client().query("BADCODE")

        self.assertEqual(caught.exception.server_error, 11081)
        self.assertIn("观战码无效", str(caught.exception))

    @patch.object(spectator_api.httpx, "AsyncClient", FakeAsyncClient)
    async def test_unparsable_body_is_unavailable(self) -> None:
        FakeAsyncClient.next_response = FakeResponse(503, ValueError("not json"))

        with self.assertRaises(SpectatorUnavailable):
            await configured_client().query("C27ED36")

    @patch.object(spectator_api.httpx, "AsyncClient", FakeAsyncClient)
    async def test_timeout_is_wrapped(self) -> None:
        FakeAsyncClient.next_error = httpx.ReadTimeout("slow")

        with self.assertRaises(SpectatorTimeout):
            await configured_client().query("C27ED36")

    @patch.object(spectator_api.httpx, "AsyncClient", FakeAsyncClient)
    async def test_connect_error_is_wrapped(self) -> None:
        FakeAsyncClient.next_error = httpx.ConnectError("boom")

        with self.assertRaises(SpectatorUnavailable) as caught:
            await configured_client().query("C27ED36")

        self.assertIn("无法连接观战服务", str(caught.exception))

    @patch.object(spectator_api.httpx, "AsyncClient", FakeAsyncClient)
    async def test_empty_roster_is_unavailable(self) -> None:
        FakeAsyncClient.next_response = FakeResponse(200, {"status": "unavailable", "players": []})

        with self.assertRaises(SpectatorUnavailable):
            await configured_client().query("C27ED36")

    def test_invalid_watch_codes_are_rejected(self) -> None:
        for code in ("", "   ", "a" * 65, "bad\x01code"):
            with self.assertRaises(spectator_api.SpectatorError):
                normalize_watch_code(code)

    def test_server_error_message_falls_back(self) -> None:
        self.assertIn("房间尚未开始", server_error_message(11079))
        self.assertIn("自定义原因", server_error_message(99999, "自定义原因"))
        self.assertIn("99999", server_error_message(99999))
        self.assertEqual(server_error_message(None, "原样返回"), "原样返回")


class SpectatorFormattingTests(unittest.TestCase):
    def snapshot(self) -> SpectatorSnapshot:
        return SpectatorSnapshot.from_payload(json.loads(json.dumps(SAMPLE_PAYLOAD)))

    def test_message_contains_players_and_cards(self) -> None:
        text = format_hand_message(self.snapshot(), watch_code="C27ED36")

        self.assertIn("C27ED36", text)
        self.assertIn("房间 943222", text)
        self.assertIn("【1】企鹅鹅大魔王（芬妮）", text)
        self.assertIn("HP 12/12", text)
        self.assertIn("金币 27", text)
        self.assertIn("攻击 1", text)
        self.assertIn("防御 2", text)
        self.assertIn("[战斗0] 以毒攻毒", text)
        self.assertIn("[战斗1·净化2] 防御(中)", text)
        # 只显示卡牌名称，不带描述。
        self.assertNotIn("回合开始恢复3点生命值", text)
        self.assertNotIn("战斗中防御方可用", text)
        # 牌面被遮蔽的玩家只显示数量，不显示任何卡牌行。
        self.assertIn("【3】幻榊陽（米米）", text)
        self.assertIn("服务器只提供数量，牌面被遮蔽", text)
        self.assertNotIn("…还有", text)

    def test_message_stays_within_budget(self) -> None:
        payload = json.loads(json.dumps(SAMPLE_PAYLOAD))
        for slot in range(4):
            payload["players"].append(
                {
                    "playerId": f"p{slot}",
                    "slot": slot,
                    "nick": "很长的玩家昵称" * 3,
                    "hp": 10,
                    "maxHp": 12,
                    "gold": 5,
                    "attack": 1,
                    "defense": 1,
                    "handKnown": True,
                    "handCount": 12,
                    "handReason": "authoritative",
                    "cards": [
                        {
                            "cardUid": index,
                            "cardId": 10000 + index,
                            "purifyNum": 0,
                            "isTemp": False,
                            "battleCost": index % 4,
                            "name": f"很长的卡牌名称{index}",
                            "description": "很长的卡牌描述文本" * 4,
                            "baseCost": 0,
                        }
                        for index in range(12)
                    ],
                }
            )

        text = format_hand_message(
            SpectatorSnapshot.from_payload(payload),
            watch_code="C27ED36",
            max_chars=900,
        )

        self.assertLessEqual(len(text), 900)
        self.assertIn("…还有", text)

    def test_message_without_players_is_not_empty(self) -> None:
        text = format_hand_message(SpectatorSnapshot(), watch_code="C27ED36")

        self.assertIn("C27ED36", text)
        self.assertIn("没有可显示的玩家手牌数据。", text)


class SpectatorCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.snapshot = SpectatorSnapshot.from_payload(json.loads(json.dumps(SAMPLE_PAYLOAD)))
        self.config = {
            "enabled": True,
            "endpoint": "http://192.168.31.2:18743",
            "timeout": 30,
            "max_chars": 3000,
            "max_name_chars": 40,
        }

    async def test_command_is_registered(self) -> None:
        spec = next(spec for spec in iter_commands() if spec.name == "查询")

        self.assertIn("观战", spec.names)
        self.assertEqual(spec.category, "游戏")

    async def test_empty_args_sends_usage(self) -> None:
        ctx = FakeContext("   ")
        with patch.object(spectator_plugin, "get_config", Mock(return_value=self.config)):
            await spectator_plugin.handle_spectator_query(ctx)

        self.assertEqual(len(ctx.sent), 1)
        self.assertIn("查询 <观战码>", str(ctx.sent[0]))

    async def test_successful_query_sends_hand(self) -> None:
        ctx = FakeContext("C27ED36")
        with (
            patch.object(spectator_plugin, "get_config", Mock(return_value=self.config)),
            patch.object(
                spectator_plugin,
                "_query_snapshot",
                AsyncMock(return_value=self.snapshot),
            ),
        ):
            await spectator_plugin.handle_spectator_query(ctx)

        self.assertEqual(len(ctx.sent), 1)
        text = str(ctx.sent[0])
        self.assertIn("以毒攻毒", text)
        self.assertIn("企鹅鹅大魔王", text)

    async def test_not_available_reports_reason(self) -> None:
        ctx = FakeContext("BADCODE")
        error = SpectatorNotAvailable("观战码无效、已过期，或对应房间当前不可观战。", server_error=11081)
        with (
            patch.object(spectator_plugin, "get_config", Mock(return_value=self.config)),
            patch.object(
                spectator_plugin,
                "_query_snapshot",
                AsyncMock(side_effect=error),
            ),
        ):
            await spectator_plugin.handle_spectator_query(ctx)

        self.assertEqual(len(ctx.sent), 1)
        self.assertIn("观战码无效", str(ctx.sent[0]))

    async def test_timeout_reports_hint(self) -> None:
        ctx = FakeContext("C27ED36")
        with (
            patch.object(spectator_plugin, "get_config", Mock(return_value=self.config)),
            patch.object(
                spectator_plugin,
                "_query_snapshot",
                AsyncMock(side_effect=SpectatorTimeout("观战查询超时（>30s）")),
            ),
        ):
            await spectator_plugin.handle_spectator_query(ctx)

        self.assertEqual(len(ctx.sent), 1)
        self.assertIn("超时", str(ctx.sent[0]))

    async def test_disabled_plugin_stays_silent(self) -> None:
        ctx = FakeContext("C27ED36")
        config = dict(self.config, enabled=False)
        with patch.object(spectator_plugin, "get_config", Mock(return_value=config)):
            await spectator_plugin.handle_spectator_query(ctx)

        self.assertEqual(ctx.sent, [])


if __name__ == "__main__":
    unittest.main()
