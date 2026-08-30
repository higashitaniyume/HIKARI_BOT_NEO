"""小红书链接触发与 QQ 卡片链接提取测试。

覆盖三个曾经失效的场景：
1. `xhslink.cn` 新短链域名没进触发白名单，自动解析永远不命中；
2. 小红书分享是 QQ 小程序卡片时，链接不在 `meta.detail_1.qqdocurl` 而在其他字段上；
3. 卡片链接经 CQ 码序列化后 `&` 变成 `&amp;`，去重时留下转义形态，
   请求出去后 `xsec_token` 变成 `amp;xsec_token`，小红书返回"无法获取作品信息"。
"""

import json
import unittest
from urllib.parse import parse_qs, urlparse

from nonebot.adapters.onebot.v11 import Message, MessageSegment, PrivateMessageEvent
from nonebot.adapters.onebot.v11.event import Sender

from plugins.media_parser.prepare import (
    _event_text,
    _extract_card_urls,
    dedupe_links,
    is_supported_platform_url,
    text_has_supported_link,
)
from third_party.astrbot_plugin_media_parser.core.parser.platform.xiaohongshu import (
    XiaohongshuParser,
)

XHS_SHORT_CN = "https://xhslink.cn/o/261pFmoXw06"
XHS_SHORT_COM = "https://xhslink.com/a/AbCdEf"
XHS_NOTE_URL = (
    "https://www.xiaohongshu.com/discovery/item/6a7c2e200000000021021911"
    "?xsec_source=app_share&type=normal&xsec_token=CBTQ47At9YNdAKeR_nMwcMhE2ta2Jk6kxXEshmOA70JOM%3D"
)
# 线上实际失败过的视频笔记分享卡片链接（QQ 图文卡片 meta.news.jumpUrl）。
XHS_CARD_URL = (
    "https://www.xiaohongshu.com/discovery/item/6a8edcb2000000000801091a"
    "?app_platform=android&ignoreEngage=true&app_version=9.43.0"
    "&share_from_user_hidden=true&xsec_source=app_share&type=video"
    "&xsec_token=CBwoVJtixWCC3ZsSM4M3ofOMfhC4Osle9n1pzk76oMYdE%3D&author_share=1"
    "&xhsshare=QQ&apptime=1787810733&share_channel=qq&code=2ANlh4kF8kG"
)


def _make_event(message: Message) -> PrivateMessageEvent:
    return PrivateMessageEvent(
        time=1000,
        self_id="10000",
        post_type="message",
        message_type="private",
        sub_type="friend",
        user_id=10001,
        message_id=100,
        message=message,
        raw_message=str(message),
        font=0,
        sender=Sender(user_id=10001),
    )


def _miniapp_card(**detail: object) -> Message:
    """构造 QQ 小程序分享卡片（小红书分享到 QQ 的形态）。"""
    payload = {
        "app": "com.tencent.miniapp_01",
        "desc": "",
        "prompt": "[QQ小程序]小红书",
        "meta": {
            "detail_1": {
                "appid": "1105781586",
                "title": "小红书",
                "desc": "这篇笔记在【小红书】等你来读~",
                "icon": "https://miniapp.gtimg.cn/public/appicon/xxx.jpg",
                "preview": "pubminishare-30161.picsz.qpic.cn/abc.jpg",
                **detail,
            }
        },
    }
    return Message(MessageSegment.json(json.dumps(payload, ensure_ascii=False)))


def _news_card(url: str) -> Message:
    """构造 QQ 图文分享卡片（小红书视频笔记分享到 QQ 的形态）。"""
    payload = {
        "app": "com.tencent.tuwen.lua",
        "desc": "新闻",
        "prompt": "[分享]长相普通的小手机",
        "meta": {
            "news": {
                "action": "",
                "tag": "小红书",
                "title": "长相普通的小手机",
                "desc": "这篇笔记在【小红书】等你来读~",
                "preview": "pubminishare-30161.picsz.qpic.cn/abc.jpg",
                "jumpUrl": url,
            }
        },
    }
    return Message(MessageSegment.json(json.dumps(payload, ensure_ascii=False)))


class TextTriggerTests(unittest.TestCase):
    def test_xhslink_cn_short_link_is_recognized(self) -> None:
        text = f"{XHS_SHORT_CN} 这篇笔记在【小红书】等你来读~"
        self.assertTrue(text_has_supported_link(text))

    def test_xhslink_com_short_link_still_recognized(self) -> None:
        self.assertTrue(text_has_supported_link(XHS_SHORT_COM))

    def test_escaped_card_text_is_recognized(self) -> None:
        """CQ 码序列化后 `&` 变成 `&amp;`，仍应命中域名标记。"""
        self.assertTrue(text_has_supported_link(XHS_NOTE_URL.replace("&", "&amp;")))

    def test_unrelated_text_not_recognized(self) -> None:
        self.assertFalse(text_has_supported_link("今天天气不错 https://example.com/x"))

    def test_vendored_parser_extracts_cn_short_link(self) -> None:
        """触发白名单放行后，vendored 解析器必须能从文本里取出这条链接。"""
        links = XiaohongshuParser().extract_links(f"{XHS_SHORT_CN} 这篇笔记在【小红书】等你来读~")
        self.assertEqual([XHS_SHORT_CN], links)


class SupportedPlatformUrlTests(unittest.TestCase):
    def test_supported_hosts(self) -> None:
        for url in (XHS_SHORT_CN, XHS_SHORT_COM, XHS_NOTE_URL, "https://b23.tv/abc"):
            with self.subTest(url=url):
                self.assertTrue(is_supported_platform_url(url))

    def test_subdomain_is_supported(self) -> None:
        self.assertTrue(is_supported_platform_url("https://m.xiaohongshu.com/explore/abc"))

    def test_lookalike_host_is_rejected(self) -> None:
        for url in (
            "https://evil-xiaohongshu.com/explore/abc",
            "https://xiaohongshu.com.evil.tld/explore/abc",
            "https://miniapp.gtimg.cn/public/appicon/xxx.jpg",
            "not a url",
        ):
            with self.subTest(url=url):
                self.assertFalse(is_supported_platform_url(url))


class CardExtractionTests(unittest.TestCase):
    def test_qqdocurl_card(self) -> None:
        event = _make_event(_miniapp_card(qqdocurl=XHS_NOTE_URL))
        self.assertEqual([XHS_NOTE_URL], _extract_card_urls(event))

    def test_url_field_only_card(self) -> None:
        """qqdocurl 缺失时，兜底扫描要能从 detail_1.url 里取到短链。"""
        event = _make_event(_miniapp_card(url=XHS_SHORT_CN))
        self.assertEqual([XHS_SHORT_CN], _extract_card_urls(event))

    def test_escaped_slashes_in_nested_json(self) -> None:
        """QQ 卡片常把 `/` 转义成 `\\/`，扫描前需要还原。"""
        raw = json.dumps(
            {"meta": {"detail_1": {"url": XHS_SHORT_CN}}}, ensure_ascii=False
        ).replace("/", "\\/")
        event = _make_event(Message(MessageSegment.json(raw)))
        self.assertEqual([XHS_SHORT_CN], _extract_card_urls(event))

    def test_non_platform_urls_are_filtered(self) -> None:
        event = _make_event(_miniapp_card())
        self.assertEqual([], _extract_card_urls(event))

    def test_qqdocurl_wins_over_scanned_fields(self) -> None:
        event = _make_event(_miniapp_card(qqdocurl=XHS_NOTE_URL, url=XHS_SHORT_CN))
        self.assertEqual([XHS_NOTE_URL, XHS_SHORT_CN], _extract_card_urls(event))

    def test_plain_text_segment_is_not_rescanned(self) -> None:
        """纯文本里的链接由原始消息文本负责，卡片提取不应重复产出。"""
        event = _make_event(Message(f"{XHS_SHORT_CN} 这篇笔记在【小红书】等你来读~"))
        self.assertEqual([], _extract_card_urls(event))

    def test_event_text_contains_card_link_for_parsing(self) -> None:
        event = _make_event(_miniapp_card(url=XHS_SHORT_CN))
        parse_text = _event_text(event)
        self.assertIn(XHS_SHORT_CN, parse_text)
        self.assertTrue(text_has_supported_link(parse_text))
        self.assertEqual([XHS_SHORT_CN], XiaohongshuParser().extract_links(parse_text))


class EscapedCardUrlTests(unittest.TestCase):
    """卡片链接必须在进入解析前还原转义，否则查询参数会整体错位。"""

    def _parsed_link(self, message: Message) -> str:
        event = _make_event(message)
        parse_text = _event_text(event)
        parser = XiaohongshuParser()
        links = dedupe_links([(url, parser) for url in parser.extract_links(parse_text)])
        self.assertEqual(1, len(links), f"应只剩一条链接: {links}")
        return links[0][0]

    def test_serialized_card_text_is_escaped(self) -> None:
        """前置事实：CQ 码序列化会把卡片 JSON 里的 `&` 写成 `&amp;`。"""
        parse_text = _event_text(_make_event(_news_card(XHS_CARD_URL)))
        self.assertIn("&amp;", parse_text)

    def test_news_card_url_is_unescaped_before_parsing(self) -> None:
        url = self._parsed_link(_news_card(XHS_CARD_URL))
        self.assertNotIn("&amp;", url)
        self.assertEqual(XHS_CARD_URL, url)

    def test_news_card_query_params_survive(self) -> None:
        """`xsec_token` / `type` 必须是真正的查询参数，而不是 `amp;xsec_token`。"""
        query = parse_qs(urlparse(self._parsed_link(_news_card(XHS_CARD_URL))).query)
        self.assertEqual(["CBwoVJtixWCC3ZsSM4M3ofOMfhC4Osle9n1pzk76oMYdE="], query["xsec_token"])
        self.assertEqual(["video"], query["type"])
        self.assertNotIn("amp;xsec_token", query)
        self.assertNotIn("amp;type", query)

    def test_miniapp_card_url_is_unescaped_before_parsing(self) -> None:
        url = self._parsed_link(_miniapp_card(qqdocurl=XHS_NOTE_URL))
        self.assertNotIn("&amp;", url)
        self.assertEqual(XHS_NOTE_URL, url)


if __name__ == "__main__":
    unittest.main()
