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
                description="解析抖音/B站/小红书/小黑盒/Steam等平台链接",
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

        self.assertIn("- 媒体解析：解析抖音/B站/小红书/小黑盒/Steam等平台链接", text)
        self.assertNotIn("媒体解析 <链接>", text)
        self.assertNotIn("B站登录", text)
        self.assertNotIn("B站Cookie", text)
        self.assertNotIn("推送", text)

    def test_category_index_shows_only_category_names(self) -> None:
        specs = [
            CommandSpec(name="帮助", aliases=(), category="基础", handler=_noop_handler),
            CommandSpec(name="统计", aliases=(), category="基础", handler=_noop_handler),
            CommandSpec(name="贴纸包", aliases=(), category="贴纸", handler=_noop_handler),
            CommandSpec(name="媒体解析", aliases=(), category="媒体", handler=_noop_handler),
            CommandSpec(name="隐藏命令", aliases=(), show_in_help=False, handler=_noop_handler),
        ]

        with (
            patch.object(command_router, "_commands", specs),
            patch.object(bot_help, "msg", side_effect=_default_message),
        ):
            text = bot_help._format_category_index()

        self.assertIn("📂 基础", text)
        self.assertIn("📂 贴纸", text)
        self.assertIn("📂 媒体（Pixiv / 抖音 / B站 / 小红书 / 小黑盒 / Steam / Instagram / Facebook / YouTube / 网易云）", text)
        self.assertNotIn("贴纸包", text)
        self.assertNotIn("隐藏命令", text)
        self.assertIn("发送「帮助 分区名」查看分区详情", text)

    def test_category_index_omits_sticker_keyword_trigger_block(self) -> None:
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
            text = bot_help._format_category_index()

        self.assertNotIn("自然触发", text)
        self.assertNotIn("贴纸关键词", text)

    def test_category_detail_lists_only_that_category(self) -> None:
        specs = [
            CommandSpec(name="统计", aliases=(), category="基础", handler=_noop_handler),
            CommandSpec(
                name="贴纸包",
                aliases=(),
                category="贴纸",
                handler=_noop_handler,
                description="贴纸包工具",
            ),
        ]

        with (
            patch.object(command_router, "_commands", specs),
            patch.object(bot_help, "msg", side_effect=_default_message),
        ):
            text = bot_help._format_category_detail("贴纸")

        self.assertIn("📂 贴纸", text)
        self.assertIn("- 贴纸包：贴纸包工具", text)
        self.assertNotIn("- 统计", text)
        self.assertIn("发送「帮助 命令名」查看单个命令的详细用法", text)

    def test_category_detail_media_includes_auto_parse(self) -> None:
        specs = [CommandSpec(name="媒体解析", aliases=(), category="媒体", handler=_noop_handler)]

        with (
            patch.object(command_router, "_commands", specs),
            patch.object(bot_help, "msg", side_effect=_default_message),
        ):
            text = bot_help._format_category_detail("媒体")

        self.assertIn("自动解析：", text)
        self.assertIn("- Pixiv 作品链接", text)

    def test_category_detail_unknown_returns_not_found(self) -> None:
        specs = [CommandSpec(name="贴纸包", aliases=(), category="贴纸", handler=_noop_handler)]

        with (
            patch.object(command_router, "_commands", specs),
            patch.object(bot_help, "msg", side_effect=_default_message),
        ):
            text = bot_help._format_category_detail("不存在")

        self.assertIn("没有找到分区：不存在", text)

    def test_resolve_help_dispatch(self) -> None:
        specs = [
            CommandSpec(
                name="贴纸包",
                aliases=(),
                category="贴纸",
                handler=_noop_handler,
                description="贴纸包工具",
            ),
            CommandSpec(name="帮助", aliases=(), category="基础", handler=_noop_handler),
        ]

        with (
            patch.object(command_router, "_commands", specs),
            patch.object(bot_help, "msg", side_effect=_default_message),
        ):
            self.assertIn("📂 基础", bot_help._resolve_help(""))
            self.assertIn("📂 贴纸", bot_help._resolve_help("贴纸"))
            self.assertIn("命令：贴纸包", bot_help._resolve_help("贴纸包"))
            self.assertIn("没有找到命令：未知", bot_help._resolve_help("未知"))


if __name__ == "__main__":
    unittest.main()