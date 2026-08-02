from __future__ import annotations

import unittest

from plugins.aiagent.tools import help as help_tool

CFG = {"tools": {"help": {"enabled": True}}}


class BotHelpToolTests(unittest.TestCase):
    def test_index_lists_all_docs(self) -> None:
        out = help_tool.execute(CFG, {})
        for name, _ in help_tool._DOC_INDEX:
            self.assertIn(name, out)
        self.assertIn("bot_help", out)

    def test_exact_filename_hit(self) -> None:
        out = help_tool.execute(CFG, {"topic": "plugins.md"})
        self.assertTrue(out.startswith("# 文档: plugins.md"))

    def test_keyword_routes_to_function_doc(self) -> None:
        # 「贴纸」应命中插件详解而不是 API 文档（API.md 标题行命中更多但被降权）
        out = help_tool.execute(CFG, {"topic": "贴纸"})
        self.assertTrue(out.startswith("# 文档: plugins.md"), out.splitlines()[0])

    def test_mixed_token_query(self) -> None:
        # 「ai聊天」无分隔符也应命中 AI Agent 章节所在文档
        out = help_tool.execute(CFG, {"topic": "ai聊天"})
        self.assertTrue(out.startswith("# 文档: plugins.md"), out.splitlines()[0])

    def test_other_docs_routable(self) -> None:
        for topic, expect in [
            ("部署", "deployment.md"),
            ("AstrBot", "astrbot.md"),
            ("核心模块", "core.md"),
            ("常见问题", "faq.md"),
            ("API", "API.md"),
        ]:
            out = help_tool.execute(CFG, {"topic": topic})
            self.assertTrue(out.startswith(f"# 文档: {expect}"), (topic, out.splitlines()[0]))

    def test_no_match_returns_guidance(self) -> None:
        out = help_tool.execute(CFG, {"topic": "不存在的主题xyz"})
        self.assertIn("没有找到", out)
        self.assertIn("文档列表", out)

    def test_disabled(self) -> None:
        self.assertFalse(help_tool.enabled({"tools": {"help": {"enabled": False}}}))
        self.assertTrue(help_tool.enabled(CFG))

    def test_definition_schema(self) -> None:
        definition = help_tool.definition()
        self.assertEqual(definition["function"]["name"], "bot_help")
        self.assertIn("topic", definition["function"]["parameters"]["properties"])


if __name__ == "__main__":
    unittest.main()
