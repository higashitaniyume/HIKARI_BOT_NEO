"""QQ 音乐解析插件测试 — ID 提取/归一化、cookie 解析、音质选择与失败归因."""

import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.adapters.onebot.v11.event import Reply, Sender

from plugins.qqmusic_parser import AutoQQMusicHandler, QQMusicCardHintHandler, _card_hint_last
from plugins.qqmusic_parser.api import QQTrackDetail, parse_netscape_cookies
from plugins.qqmusic_parser.downloader import format_selector
from plugins.qqmusic_parser.errors import (
    QQMusicCookieRequiredError,
    QQMusicNoFormatError,
    QQMusicResolveError,
    QQMusicVipRequiredError,
)
from plugins.qqmusic_parser.parser import (
    build_song_url,
    classify_song_id,
    collect_song_refs,
    extract_song_refs,
    has_qqmusic_ref,
)
from plugins.qqmusic_parser.processing import explain_no_format, user_message_for_error

# 真实 QQ 音乐分享卡片（来自线上日志）。
# 注意 musicUrl 里带 "songid=&songmid=..." —— 空 songid 不能被误判成数字 ID。
QQ_MUSIC_CARD = json.dumps({
    "app": "com.tencent.music.lua",
    "meta": {
        "music": {
            "app_type": 1,
            "appid": 100497308,
            "desc": "洛克王国：世界",
            "jumpUrl": (
                "https://i.y.qq.com/v8/playsong.html?platform=11&appshare=android_qq"
                "&appversion=20080508&hosteuin=oKciNKSkoK-Pov**&songmid=003dKInI1dmvj6"
                "&type=0&appsongtype=1&_wv=1&source=qq&ADTAG=qfshare"
            ),
            "musicUrl": (
                "http://c6.y.qq.com/rsc/fcgi-bin/fcg_pyq_play.fcg?songid=&songmid=003dKInI1dmvj6"
                "&songtype=1&fromtag=50&uin=1839751241&code=AE0E5"
            ),
            "title": "月亮点亮的入口（游戏原声·S4赛季大厅）",
        }
    },
}, ensure_ascii=False)

# 真实 cookie 文件片段（含注释、空值、非 qq 域）。
COOKIE_SAMPLE = (
    "# Netscape HTTP Cookie File\n"
    "# https://curl.haxx.se/rfc/cookie_spec.html\n"
    "\n"
    ".qq.com\tTRUE\t/\tFALSE\t1819552084\t_qimei_q36\t\n"
    ".y.qq.com\tTRUE\t/\tFALSE\t1824215232\tts_uid\t7927188812\n"
    ".qq.com\tTRUE\t/\tFALSE\t1789914427\tqqmusic_key\tQ_H_L_test_key\n"
    ".qq.com\tTRUE\t/\tFALSE\t1789914427\tuin\t3433559280\n"
    ".qq.com\tTRUE\t/\tFALSE\t1813574017\tfqm_pvqid\t3390e29a-089b-4e38-adbc-371b722f556d\n"
    "example.com\tTRUE\t/\tFALSE\t1789914427\tother_site_secret\tSHOULD_NOT_LEAK\n"
)


def _make_private(text: str = "", *, json_card: str = "") -> PrivateMessageEvent:
    segments = []
    if json_card:
        segments.append(MessageSegment(type="json", data={"data": json_card}))
    if text:
        segments.append(MessageSegment.text(text))
    msg = Message(segments)
    return PrivateMessageEvent(
        time=1000, self_id="10000", post_type="message", message_type="private",
        sub_type="friend", user_id=10001, message_id=100, message=msg,
        raw_message=str(msg), font=0, sender=Sender(user_id=10001),
    )


def _make_group(
    text: str = "",
    *,
    group_id: int = 111,
    at_self: bool = False,
    at_mid: bool = False,
    json_card: str = "",
    text_first: bool = False,
) -> GroupMessageEvent:
    body = Message()
    if at_mid:
        body.append(MessageSegment.at("10000"))
    if text_first and text:
        body.append(MessageSegment.text(text))
    if json_card:
        body.append(MessageSegment(type="json", data={"data": json_card}))
    if text and not text_first:
        body.append(MessageSegment.text(text))
    msg = Message(body)
    return GroupMessageEvent(
        time=2000, self_id="10000", post_type="message", message_type="group",
        sub_type="normal", group_id=group_id, user_id=10001, message_id=500,
        message=msg, raw_message=str(msg), font=0, sender=Sender(user_id=10001),
        to_me=at_self,
    )


def _set_reply(event: GroupMessageEvent, message: Message, *, reply_id: int = 999) -> GroupMessageEvent:
    event.reply = Reply(
        time=1000, message_type="group", message_id=reply_id, real_id=reply_id,
        sender=Sender(user_id=10002), message=message,
    )
    return event


def _cfg(**overrides) -> dict:
    cfg = {
        "enabled": True,
        "auto_parse": True,
        "auto_parse_groups": {"enable": False, "groups": []},
        "card_hint": {"enabled": True, "cooldown_seconds": 300},
        "permissions": {},
    }
    cfg.update(overrides)
    return cfg


class TestSongRefExtraction(unittest.TestCase):
    def test_classify_picks_songid_for_digits(self):
        self.assertEqual(classify_song_id("587897337").songid, "587897337")
        self.assertEqual(classify_song_id("587897337").songmid, "")
        self.assertEqual(classify_song_id("003dKInI1dmvj6").songmid, "003dKInI1dmvj6")
        self.assertEqual(classify_song_id("003dKInI1dmvj6").songid, "")

    def test_extract_songmid_query(self):
        text = "https://i.y.qq.com/v8/playsong.html?platform=11&songmid=003dKInI1dmvj6&type=0"
        refs = extract_song_refs(text)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].songmid, "003dKInI1dmvj6")

    def test_extract_songid_query(self):
        text = "https://i.y.qq.com/v8/playsong.html?songid=587897337#webchat_redirect"
        refs = extract_song_refs(text)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].songid, "587897337")
        self.assertTrue(refs[0].needs_resolve)

    def test_extract_empty_songid_query_is_ignored(self):
        # musicUrl 里的 "songid=&songmid=..."：空 songid 不能产生引用
        text = "http://c6.y.qq.com/rsc/fcgi-bin/fcg_pyq_play.fcg?songid=&songmid=003dKInI1dmvj6&songtype=1"
        refs = extract_song_refs(text)
        self.assertEqual([ref.key for ref in refs], ["003dKInI1dmvj6"])

    def test_extract_songdetail_variants(self):
        for url in (
            "https://y.qq.com/n/ryqq/songDetail/003dKInI1dmvj6",
            "https://y.qq.com/n/ryqq_v2/songDetail/003dKInI1dmvj6",
            "https://y.qq.com/n/yqq/song/003dKInI1dmvj6.html",
        ):
            with self.subTest(url=url):
                refs = extract_song_refs(url)
                self.assertEqual([ref.songmid for ref in refs], ["003dKInI1dmvj6"])

    def test_songdetail_numeric_becomes_songid(self):
        # yt-dlp 会把数字当 song_mid 传给接口并拿到空壳，必须按 songid 走换算
        refs = extract_song_refs("https://y.qq.com/n/ryqq/songDetail/587897337")
        self.assertEqual([ref.songid for ref in refs], ["587897337"])
        self.assertEqual(refs[0].songmid, "")

    def test_amp_escaped_url(self):
        text = "https://i.y.qq.com/v8/playsong.html?a=1&amp;songmid=003dKInI1dmvj6&amp;b=2"
        self.assertEqual([r.songmid for r in extract_song_refs(text)], ["003dKInI1dmvj6"])

    def test_no_ref_for_unrelated_url(self):
        self.assertFalse(has_qqmusic_ref("https://music.163.com/song/33894312"))
        self.assertFalse(has_qqmusic_ref("你好"))

    def test_build_song_url(self):
        self.assertEqual(
            build_song_url("003dKInI1dmvj6"),
            "https://y.qq.com/n/ryqq/songDetail/003dKInI1dmvj6",
        )

    def test_collect_from_real_card_dedupes(self):
        event = _make_group(json_card=QQ_MUSIC_CARD)
        refs = collect_song_refs(event)
        self.assertEqual([ref.key for ref in refs], ["003dKInI1dmvj6"])

    def test_collect_merges_text_and_card(self):
        # 扫描顺序 = 消息里片段的实际出现顺序（卡片在前，正文在后）
        event = _make_group(
            "https://y.qq.com/n/ryqq/songDetail/004c1oSb2qBnTl",
            json_card=QQ_MUSIC_CARD,
        )
        keys = [ref.key for ref in collect_song_refs(event)]
        self.assertEqual(keys, ["003dKInI1dmvj6", "004c1oSb2qBnTl"])

    def test_collect_text_before_card(self):
        event = _make_group(
            "https://y.qq.com/n/ryqq/songDetail/004c1oSb2qBnTl",
            json_card=QQ_MUSIC_CARD,
            text_first=True,
        )
        keys = [ref.key for ref in collect_song_refs(event)]
        self.assertEqual(keys, ["004c1oSb2qBnTl", "003dKInI1dmvj6"])


class TestCookieParsing(unittest.TestCase):
    def test_parses_real_sample(self):
        jar = parse_netscape_cookies(COOKIE_SAMPLE)
        self.assertEqual(jar["uin"], "3433559280")
        self.assertEqual(jar["qqmusic_key"], "Q_H_L_test_key")
        self.assertEqual(jar["fqm_pvqid"], "3390e29a-089b-4e38-adbc-371b722f556d")
        self.assertEqual(jar["ts_uid"], "7927188812")

    def test_empty_value_is_kept(self):
        jar = parse_netscape_cookies(COOKIE_SAMPLE)
        self.assertEqual(jar["_qimei_q36"], "")

    def test_comments_are_skipped(self):
        jar = parse_netscape_cookies(COOKIE_SAMPLE)
        self.assertNotIn("# Netscape HTTP Cookie File", jar)

    def test_non_qq_domain_excluded(self):
        jar = parse_netscape_cookies(COOKIE_SAMPLE)
        self.assertNotIn("other_site_secret", jar)

    def test_httponly_prefix_is_parsed(self):
        text = "#HttpOnly_.qq.com\tTRUE\t/\tFALSE\t1789914427\tqm_keyst\tabc123\n"
        jar = parse_netscape_cookies(text)
        self.assertEqual(jar["qm_keyst"], "abc123")

    def test_malformed_lines_ignored(self):
        jar = parse_netscape_cookies("garbage\n.qq.com\tTRUE\n")
        self.assertEqual(jar, {})


class TestFormatSelector(unittest.TestCase):
    def test_missing_priority_falls_back_to_anonymous_tiers(self):
        selector = format_selector({})
        self.assertTrue(selector.startswith("128mp3/96aac/48aac"))

    def test_configured_priority(self):
        selector = format_selector({"format_priority": ["flac", "320mp3", "128mp3"]})
        self.assertTrue(selector.startswith("flac/320mp3/128mp3"))

    def test_unknown_formats_dropped(self):
        selector = format_selector({"format_priority": ["bogus", "320mp3"]})
        self.assertTrue(selector.startswith("320mp3"))

    def test_all_unknown_falls_back(self):
        selector = format_selector({"format_priority": ["bogus"]})
        self.assertTrue(selector.startswith("128mp3/96aac/48aac"))

    def test_dedupe(self):
        selector = format_selector({"format_priority": ["128mp3", "128mp3", "flac"]})
        self.assertTrue(selector.startswith("128mp3/flac"))

    def test_non_list_is_tolerated(self):
        selector = format_selector({"format_priority": "flac"})
        self.assertTrue(selector.startswith("128mp3"))


class TestFailureAttribution(unittest.TestCase):
    def _detail(self, pay_play: int) -> QQTrackDetail:
        return QQTrackDetail(songmid="003dKInI1dmvj6", name="月亮点亮的入口", pay_play=pay_play)

    def test_vip_song_reported_as_vip(self):
        with patch("plugins.qqmusic_parser.processing.get_cookiefile", return_value=Path(__file__)):
            exc = explain_no_format(self._detail(1), {})
        self.assertIsInstance(exc, QQMusicVipRequiredError)

    def test_free_song_without_cookie_reported_as_cookie_missing(self):
        with patch("plugins.qqmusic_parser.processing.get_cookiefile", return_value=None):
            exc = explain_no_format(self._detail(0), {})
        self.assertIsInstance(exc, QQMusicCookieRequiredError)

    def test_free_song_with_cookie_reported_as_unavailable(self):
        with patch("plugins.qqmusic_parser.processing.get_cookiefile", return_value=Path(__file__)):
            exc = explain_no_format(self._detail(0), {})
        self.assertIsInstance(exc, QQMusicNoFormatError)
        self.assertNotIsInstance(exc, QQMusicVipRequiredError)

    def test_resolve_error_message(self):
        text = user_message_for_error(QQMusicResolveError("空 track_info"), {})
        self.assertIn("无法识别", text)

    def test_cookie_error_message_includes_path(self):
        cfg = {"cookiefile": "BotData/cookies/qqmusic.txt"}
        text = user_message_for_error(QQMusicCookieRequiredError("缺少 cookie"), cfg)
        self.assertIn("BotData/cookies/qqmusic.txt", text)

    def test_vip_error_message(self):
        text = user_message_for_error(QQMusicVipRequiredError("VIP"), {})
        self.assertIn("VIP", text)

    def test_generic_error_includes_reason(self):
        from plugins.qqmusic_parser.errors import QQMusicError

        text = user_message_for_error(QQMusicError("网络超时"), {})
        self.assertIn("网络超时", text)


class TestAutoHandlerMatch(unittest.TestCase):
    async def _match(self, event, cfg=None):
        handler = AutoQQMusicHandler()
        with patch(
            "plugins.qqmusic_parser.get_config",
            return_value=cfg if cfg is not None else _cfg(),
        ):
            return await handler.match(event, str(event.get_message()))

    def test_private_link_matches(self):
        event = _make_private("https://y.qq.com/n/ryqq/songDetail/003dKInI1dmvj6")
        self.assertTrue(asyncio.run(self._match(event)))

    def test_private_songid_link_matches(self):
        event = _make_private("https://i.y.qq.com/v8/playsong.html?songid=587897337#webchat_redirect")
        self.assertTrue(asyncio.run(self._match(event)))

    def test_private_card_matches(self):
        event = _make_private(json_card=QQ_MUSIC_CARD)
        self.assertTrue(asyncio.run(self._match(event)))

    def test_group_link_without_at_does_not_match(self):
        event = _make_group("https://y.qq.com/n/ryqq/songDetail/003dKInI1dmvj6")
        self.assertFalse(asyncio.run(self._match(event)))

    def test_group_link_with_at_matches(self):
        event = _make_group("https://y.qq.com/n/ryqq/songDetail/003dKInI1dmvj6", at_self=True)
        self.assertTrue(asyncio.run(self._match(event)))

    def test_group_at_in_middle_matches(self):
        event = _make_group(
            "https://y.qq.com/n/ryqq/songDetail/003dKInI1dmvj6", at_mid=True,
        )
        self.assertTrue(asyncio.run(self._match(event)))

    def test_auto_parse_group_matches_without_at(self):
        event = _make_group("https://y.qq.com/n/ryqq/songDetail/003dKInI1dmvj6")
        cfg = _cfg(auto_parse_groups={"enable": True, "groups": ["111"]})
        self.assertTrue(asyncio.run(self._match(event, cfg)))

    def test_group_at_with_quoted_card_matches(self):
        event = _make_group(at_self=True)
        _set_reply(event, Message([MessageSegment(type="json", data={"data": QQ_MUSIC_CARD})]))
        self.assertTrue(asyncio.run(self._match(event)))

    def test_group_at_without_any_ref_does_not_match(self):
        event = _make_group("你好", at_self=True)
        self.assertFalse(asyncio.run(self._match(event)))

    def test_disabled_plugin_does_not_match(self):
        event = _make_private("https://y.qq.com/n/ryqq/songDetail/003dKInI1dmvj6")
        self.assertFalse(asyncio.run(self._match(event, _cfg(enabled=False))))

    def test_auto_parse_off_does_not_match(self):
        event = _make_private("https://y.qq.com/n/ryqq/songDetail/003dKInI1dmvj6")
        self.assertFalse(asyncio.run(self._match(event, _cfg(auto_parse=False))))


class TestCardHintHandler(unittest.TestCase):
    def setUp(self):
        _card_hint_last.clear()

    async def _match(self, event, cfg=None):
        handler = QQMusicCardHintHandler()
        with patch(
            "plugins.qqmusic_parser.get_config",
            return_value=cfg if cfg is not None else _cfg(),
        ):
            return await handler.match(event, str(event.get_message()))

    def test_group_card_without_at_triggers_hint(self):
        event = _make_group(json_card=QQ_MUSIC_CARD)
        self.assertTrue(asyncio.run(self._match(event)))

    def test_group_text_link_triggers_hint(self):
        event = _make_group("https://y.qq.com/n/ryqq/songDetail/003dKInI1dmvj6")
        self.assertTrue(asyncio.run(self._match(event)))

    def test_at_bot_no_hint(self):
        event = _make_group("https://y.qq.com/n/ryqq/songDetail/003dKInI1dmvj6", at_self=True)
        self.assertFalse(asyncio.run(self._match(event)))

    def test_auto_parse_group_no_hint(self):
        event = _make_group(json_card=QQ_MUSIC_CARD)
        cfg = _cfg(auto_parse_groups={"enable": True, "groups": ["111"]})
        self.assertFalse(asyncio.run(self._match(event, cfg)))

    def test_private_no_hint(self):
        event = _make_private("https://y.qq.com/n/ryqq/songDetail/003dKInI1dmvj6")
        self.assertFalse(asyncio.run(self._match(event)))

    def test_hint_disabled(self):
        event = _make_group(json_card=QQ_MUSIC_CARD)
        cfg = _cfg(card_hint={"enabled": False, "cooldown_seconds": 300})
        self.assertFalse(asyncio.run(self._match(event, cfg)))

    def test_cooldown_suppresses_second_hint(self):
        event = _make_group(json_card=QQ_MUSIC_CARD)
        handler = QQMusicCardHintHandler()
        with patch("plugins.qqmusic_parser.get_config", return_value=_cfg()):
            self.assertTrue(asyncio.run(handler.match(event, "")))
            asyncio.run(handler.handle(AsyncMockBot(), event))
            self.assertFalse(asyncio.run(handler.match(event, "")))

    def test_other_group_not_affected_by_cooldown(self):
        event_a = _make_group(json_card=QQ_MUSIC_CARD, group_id=111)
        event_b = _make_group(json_card=QQ_MUSIC_CARD, group_id=222)
        handler = QQMusicCardHintHandler()
        with patch("plugins.qqmusic_parser.get_config", return_value=_cfg()):
            asyncio.run(handler.handle(AsyncMockBot(), event_a))
            self.assertTrue(asyncio.run(handler.match(event_b, "")))


class AsyncMockBot:
    """记录发送内容的假 Bot。"""

    def __init__(self) -> None:
        self.sent: list[Message] = []

    async def send(self, event, message) -> None:
        self.sent.append(message)


if __name__ == "__main__":
    unittest.main()
