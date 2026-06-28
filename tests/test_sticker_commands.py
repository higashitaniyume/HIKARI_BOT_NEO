from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

import plugins.bot_help as bot_help
import plugins.sticker_trigger as sticker_plugin
from core import bot_messages
from core.command_router import iter_commands


def _default_message(key: str, **kwargs) -> str:
    current = bot_messages.DEFAULT_MESSAGES
    for part in key.split("."):
        current = current[part]
    text = str(current)
    return text.format(**kwargs) if kwargs else text


class FakeContext:
    def __init__(self, args: str = "") -> None:
        self.args = args
        self.sent: list[object] = []

    async def send(self, message) -> None:
        self.sent.append(message)


class StickerCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_sticker_pack_dispatcher_routes_spaced_subcommands(self) -> None:
        seen_args: list[str] = []

        with patch.object(
            sticker_plugin,
            "cmd_sticker_collage",
            AsyncMock(side_effect=lambda inner_ctx: seen_args.append(inner_ctx.args)),
        ) as collage:
            ctx = FakeContext("拼图 猫猫虫")
            await sticker_plugin.cmd_sticker_pack(ctx)

        collage.assert_awaited_once()
        self.assertEqual(seen_args, ["猫猫虫"])
        self.assertEqual(ctx.args, "拼图 猫猫虫")

    async def test_sticker_pack_defaults_to_help_for_unknown_subcommand(self) -> None:
        with patch.object(sticker_plugin, "cmd_sticker_pack_help", AsyncMock()) as help_command:
            await sticker_plugin.cmd_sticker_pack(FakeContext("不存在"))

        help_command.assert_awaited_once()

    def test_sticker_pack_is_single_registered_top_level_command(self) -> None:
        names = [spec.name for spec in iter_commands()]

        self.assertIn("贴纸包", names)
        self.assertNotIn("随机贴纸", names)
        self.assertNotIn("拼图", names)
        self.assertNotIn("贴纸包统计", names)
        self.assertNotIn("贴纸包列表", names)
        self.assertNotIn("贴纸包预览", names)

    def test_help_sticker_pack_shows_subcommand_detail(self) -> None:
        spec = next(spec for spec in iter_commands() if spec.name == "贴纸包")
        with patch.object(bot_help, "msg", side_effect=_default_message):
            detail = bot_help._format_command_detail(spec)

        self.assertEqual(spec.usage, "贴纸包")
        self.assertEqual(spec.detail_key, "sticker.help")
        self.assertIn("详细用法", detail)
        self.assertIn("贴纸包 拼图", detail)


if __name__ == "__main__":
    unittest.main()
