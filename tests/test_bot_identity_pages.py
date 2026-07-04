from __future__ import annotations

import unittest
from unittest.mock import patch

from plugins.bot_admin import pages as bot_admin_pages
from plugins.media_detail_web import pages as media_detail_pages


class BotIdentityPageTests(unittest.TestCase):
    def test_bot_admin_pages_use_configured_bot_name(self) -> None:
        with patch("core.bot_identity.load_main_config", return_value={"bot": {"name": "测试Bot"}}):
            index = bot_admin_pages._html_page().decode("utf-8")
            login = bot_admin_pages._login_page().decode("utf-8")

        self.assertIn("<title>测试Bot 后台</title>", index)
        self.assertIn('<meta name="bot-name" content="测试Bot">', index)
        self.assertIn("<title>测试Bot 贴纸管理登录</title>", login)
        self.assertNotIn("HIKARI", index)
        self.assertNotIn("HIKARI", login)

    def test_media_detail_page_uses_configured_bot_name(self) -> None:
        with patch("core.bot_identity.load_main_config", return_value={"bot": {"name": "测试Bot"}}):
            page = media_detail_pages.index_page().decode("utf-8")

        self.assertIn('<p class="subtitle">测试Bot</p>', page)
        self.assertNotIn("HIKARI BOT NEO", page)


if __name__ == "__main__":
    unittest.main()
