"""Tests for URL dedup normalization in the aggregated media parser."""

import unittest

from plugins.media_parser.prepare import dedupe_links


class MediaParserDedupeTests(unittest.TestCase):
    def test_escaped_and_plain_query_params_are_the_same_link(self) -> None:
        """QQ 卡片经 CQ 码序列化后 `&` 会变成 `&amp;`，两条文本不同但指向同一链接。"""
        plain = (
            "https://b23.tv/0cQiWis?share_medium=android"
            "&share_source=qq&bbid=XU362F83D7578FF9565F48A6F8925F085D060&ts=1785753746566"
        )
        escaped = plain.replace("&", "&amp;")

        links = [(plain, "bilibili"), (escaped, "bilibili")]

        self.assertEqual([(plain, "bilibili")], dedupe_links(links))

    def test_escaped_link_is_unescaped_for_parsing(self) -> None:
        """转义形态先出现时，输出必须是还原后的地址——转义的 `&amp;` 直接请求会丢查询参数。"""
        escaped = "https://b23.tv/0cQiWis?share_medium=android&amp;ts=1"
        plain = "https://b23.tv/0cQiWis?share_medium=android&ts=1"

        links = [(escaped, "bilibili"), (plain, "bilibili")]

        self.assertEqual([(plain, "bilibili")], dedupe_links(links))

    def test_cq_comma_entity_is_unescaped(self) -> None:
        """CQ 码把 `,` 序列化成 `&#44;`，同样要还原。"""
        escaped = "https://b23.tv/0cQiWis?p=1&#44;2"
        plain = "https://b23.tv/0cQiWis?p=1,2"

        self.assertEqual([(plain, "bilibili")], dedupe_links([(escaped, "bilibili")]))

    def test_blank_link_is_dropped(self) -> None:
        self.assertEqual([], dedupe_links([("   ", "bilibili")]))

    def test_distinct_links_are_all_kept(self) -> None:
        links = [
            ("https://b23.tv/aaa", "bilibili"),
            ("https://b23.tv/bbb", "bilibili"),
        ]

        self.assertEqual(links, dedupe_links(links))
