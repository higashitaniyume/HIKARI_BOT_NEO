from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

import plugins.bot_help as bot_help
import plugins.tg_sticker_parser as tg_plugin
from core import bot_messages
from core.command_router import iter_commands


def _default_message(key: str, **kwargs) -> str:
    current = bot_messages.DEFAULT_MESSAGES
    for part in key.split("."):
        current = current[part]
    text = str(current)
    return text.format(**kwargs) if kwargs else text


class FakeEvent:
    def get_user_id(self) -> str:
        return "10001"


class FakeContext:
    def __init__(self, args: str = "") -> None:
        self.args = args
        self.bot = object()
        self.event = FakeEvent()
        self.sent: list[object] = []

    async def send(self, message) -> None:
        self.sent.append(message)


class TgStickerCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_tg_sticker_command_requires_link_as_first_arg(self) -> None:
        ctx = FakeContext("zip https://t.me/addstickers/Foo")
        with patch.object(tg_plugin, "msg", side_effect=_default_message):
            await tg_plugin.cmd_tg_sticker(ctx)

        self.assertIn("tg贴纸 <https://t.me/addstickers/贴纸包名>", str(ctx.sent[0]))

    async def test_tg_sticker_command_parses_link_and_following_options(self) -> None:
        ctx = FakeContext("https://t.me/addstickers/Foo zip refresh nosave name=猫猫虫")
        with patch.object(tg_plugin, "handle_tg_sticker_request", AsyncMock()) as handler:
            await tg_plugin.cmd_tg_sticker(ctx)

        handler.assert_awaited_once()
        self.assertEqual(handler.await_args.args[2], "Foo")
        options = handler.await_args.args[3]
        self.assertTrue(options.use_zip)
        self.assertTrue(options.refresh)
        self.assertFalse(options.save_pack)
        self.assertEqual(options.trigger_keyword, "猫猫虫")

    def test_tg_sticker_is_registered_as_command(self) -> None:
        spec = next(spec for spec in iter_commands() if spec.name == "tg贴纸")

        self.assertEqual(spec.usage, "tg贴纸 <链接> [参数]")
        self.assertEqual(spec.detail_key, "tg_sticker.help")

    def test_help_tg_sticker_shows_command_detail(self) -> None:
        spec = next(spec for spec in iter_commands() if spec.name == "tg贴纸")
        with patch.object(bot_help, "msg", side_effect=_default_message):
            detail = bot_help._format_command_detail(spec)

        self.assertIn("详细用法", detail)
        self.assertIn("tg贴纸 <链接>", detail)


if __name__ == "__main__":
    unittest.main()
