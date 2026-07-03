from __future__ import annotations

import unittest
from unittest.mock import patch

import plugins.bot_help as bot_help
from core import bot_messages, command_router
from core.command_router import CommandSpec


def _noop_handler(ctx) -> None:
    return None


def _default_message(key: str, **kwargs) -> str:
    current = bot_messages.DEFAULT_MESSAGES
    for part in key.split("."):
        current = current[part]
    text = str(current)
    return text.format(**kwargs) if kwargs else text


class BotHelpTests(unittest.TestCase):
    def test_command_list_uses_public_name_not_usage(self) -> None:
        specs = [
            CommandSpec(
                name="媒体解析",
                aliases=(),
                handler=_noop_handler,
                description="解析抖音/B站/小红书/小黑盒等平台链接",
                usage="媒体解析 <链接>",
            ),
            CommandSpec(
                name="B站登录",
                aliases=("B站Cookie",),
                handler=_noop_handler,
                description="向超级管理员私发 B站扫码登录二维码",
                usage="B站登录",
                show_in_help=False,
            ),
            CommandSpec(
                name="推送",
                aliases=("push",),
                handler=_noop_handler,
                description="管理定时推送框架",
                usage="推送 [状态|源|触发 <任务ID>]",
                show_in_help=False,
            ),
        ]

        with (
            patch.object(command_router, "_commands", specs),
            patch.object(bot_help, "msg", side_effect=_default_message),
        ):
            text = bot_help._format_command_list()

        self.assertIn("- 媒体解析：解析抖音/B站/小红书/小黑盒等平台链接", text)
        self.assertNotIn("媒体解析 <链接>", text)
        self.assertNotIn("B站登录", text)
        self.assertNotIn("B站Cookie", text)
        self.assertNotIn("推送", text)

    def test_summary_help_omits_sticker_keyword_trigger_block(self) -> None:
        specs = [
            CommandSpec(
                name="贴纸包",
                aliases=(),
                handler=_noop_handler,
                description="贴纸包工具",
                usage="贴纸包",
            )
        ]

        with (
            patch.object(command_router, "_commands", specs),
            patch.object(bot_help, "msg", side_effect=_default_message),
        ):
            text = bot_help._summary_help()

        self.assertNotIn("自然触发", text)
        self.assertNotIn("贴纸关键词", text)


if __name__ == "__main__":
    unittest.main()
