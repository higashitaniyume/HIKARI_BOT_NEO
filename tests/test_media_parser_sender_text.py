"""解析结果文本组装测试：`原始链接` 行受 message.text_metadata.show_url 控制。"""

import unittest

from core.defaults import DEFAULT_MEDIA_PARSER_CONFIG
from plugins.media_parser.sender import build_metadata_text

METADATA = {
    "platform": "xiaohongshu",
    "title": "长相普通的小手机",
    "author": "某人",
    "source_url": "https://www.xiaohongshu.com/discovery/item/6a7ef284000000002c004642?xsec_token=abc",
    "desc": "正文内容",
    "video_urls": ["https://sns-video.xhscdn.com/stream/1.mp4"],
}


class MetadataTextUrlLineTests(unittest.TestCase):
    def _build(self, **kwargs: object) -> str:
        return build_metadata_text(dict(METADATA), max_desc_chars=600, **kwargs)

    def test_url_line_shown_by_default(self) -> None:
        self.assertIn("原始链接：", self._build())

    def test_url_line_hidden_when_disabled(self) -> None:
        text = self._build(show_url=False)
        self.assertNotIn("原始链接", text)
        self.assertNotIn(METADATA["source_url"], text)

    def test_other_fields_survive_when_url_hidden(self) -> None:
        """关掉链接行不能影响标题/作者/正文。"""
        text = self._build(show_url=False)
        self.assertIn("长相普通的小手机", text)
        self.assertIn("某人", text)
        self.assertIn("正文内容", text)
        self.assertNotIn("\n\n\n", text)

    def test_default_config_exposes_switch(self) -> None:
        """默认配置要带上这个键，用户 JSON 才能通过深合并拿到它。"""
        self.assertIs(
            True,
            DEFAULT_MEDIA_PARSER_CONFIG["message"]["text_metadata"]["show_url"],
        )


if __name__ == "__main__":
    unittest.main()
