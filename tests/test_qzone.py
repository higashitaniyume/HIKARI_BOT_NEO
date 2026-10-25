from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import nonebot

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init(driver="nonebot.drivers.none:Driver")

from core.ai_tool_registry import get_ai_tool
from third_party.qzone_sdk.utils.gtk import compute_g_tk

import plugins.qzone as qzone
from plugins.qzone import service

_COOKIE = "uin=o3946388948; skey=abc123; p_skey=xyz789"


class _FakeBot:
    self_id = "3946388948"

    def __init__(self, cookies: str | BaseException = _COOKIE) -> None:
        self._cookies = cookies

    async def call_api(self, api: str, **params: object) -> object:
        if isinstance(self._cookies, BaseException):
            raise self._cookies
        return {"cookies": self._cookies, "token": 123}


class _FakeEvent:
    def __init__(self, user_id: str = "42", session_id: str = "private:42") -> None:
        self._user_id = user_id
        self._session_id = session_id

    def get_user_id(self) -> str:
        return self._user_id

    def get_session_id(self) -> str:
        return self._session_id


class _FakeContext:
    def __init__(self, args: str, *, bot: object | None = None, event: object | None = None) -> None:
        self.args = args
        self.bot = bot or _FakeBot()
        self.event = event or _FakeEvent()
        self.sent: list[str] = []

    async def send(self, message: object) -> None:
        self.sent.append(str(message))


def _feed_item(**overrides: object) -> service.FeedItem:
    data = {
        "tid": "153635dbae78dd6ab6d50a00",
        "uin": "3677697557",
        "nickname": "罗卜回",
        "content": "今天的晚霞",
        "image_count": 1,
        "abstime": 1792899246,
        "appid": 311,
        "typeid": 0,
        "unikey": "http://user.qzone.qq.com/3677697557/mood/153635dbae78dd6ab6d50a00",
        "curkey": "http://user.qzone.qq.com/3677697557/mood/153635dbae78dd6ab6d50a00",
    }
    data.update(overrides)
    return service.FeedItem(**data)  # type: ignore[arg-type]


class PublishArgsTests(unittest.TestCase):
    def test_default_visibility_keeps_text(self) -> None:
        visible, content = qzone.parse_publish_args("今天天气不错", default_visible="public")
        self.assertEqual((visible, content), (1, "今天天气不错"))

    def test_keyword_sets_visibility(self) -> None:
        self.assertEqual(
            qzone.parse_publish_args("私密 只给自己看", default_visible="public"),
            (4, "只给自己看"),
        )
        self.assertEqual(
            qzone.parse_publish_args("好友 给好友看", default_visible="public"),
            (3, "给好友看"),
        )

    def test_unknown_head_is_treated_as_content(self) -> None:
        visible, content = qzone.parse_publish_args("好友们 看这里", default_visible="friends")
        self.assertEqual((visible, content), (3, "好友们 看这里"))

    def test_visibility_keyword_without_content(self) -> None:
        self.assertEqual(qzone.parse_publish_args("私密", default_visible="public"), (4, ""))

    def test_empty_args(self) -> None:
        self.assertEqual(qzone.parse_publish_args("   ", default_visible="private"), (4, ""))


class AuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_auth_builds_g_tk(self) -> None:
        auth = await service.fetch_auth(_FakeBot())
        self.assertEqual(auth.uin, "3946388948")
        self.assertEqual(auth.g_tk, compute_g_tk("xyz789"))

    async def test_fetch_auth_requires_p_skey(self) -> None:
        with self.assertRaises(service.QzoneLoginError):
            await service.fetch_auth(_FakeBot("uin=o3946388948; skey=abc123"))

    async def test_fetch_auth_wraps_napcat_failure(self) -> None:
        with self.assertRaises(service.QzoneLoginError):
            await service.fetch_auth(_FakeBot(RuntimeError("ws closed")))


class FeedParsingTests(unittest.TestCase):
    def test_feed_item_is_normalized(self) -> None:
        item = service._feed_item_from_sdk(
            {
                "key": "abcd1234",
                "uin": "123456",
                "nickname": " 小明 ",
                "content": "\t\t今天  不错\n",
                "images": ["a.jpg", "b.jpg"],
                "abstime": "1792899246",
                "appid": "311",
                "typeid": "0",
            }
        )
        assert item is not None
        self.assertEqual(item.tid, "abcd1234")
        self.assertEqual(item.content, "今天 不错")
        self.assertEqual(item.image_count, 2)
        self.assertEqual(item.abstime, 1792899246)
        self.assertEqual(item.unikey, "http://user.qzone.qq.com/123456/mood/abcd1234")

    def test_ads_and_empty_items_are_dropped(self) -> None:
        self.assertIsNone(service._feed_item_from_sdk({"key": "advertisement_outlink_0", "uin": "0"}))
        self.assertIsNone(
            service._feed_item_from_sdk({"key": "abc", "uin": "123", "appid": "6600", "content": "广告"})
        )
        self.assertIsNone(service._feed_item_from_sdk({"key": "abc", "uin": "123", "content": "  "}))

    def test_image_only_item_is_kept(self) -> None:
        item = service._feed_item_from_sdk(
            {"key": "abc", "uin": "123", "content": "", "images": ["a.jpg"], "appid": "311"}
        )
        assert item is not None
        self.assertEqual(item.image_count, 1)

    def test_escaped_whitespace_and_emoji_are_cleaned(self) -> None:
        item = service._feed_item_from_sdk(
            {
                "key": "abc",
                "uin": "123",
                "content": "\\t\\t\t原来是这样[em]e400867[/em]\\n",
                "images": ["a.jpg"],
                "appid": "311",
            }
        )
        assert item is not None
        self.assertEqual(item.content, "原来是这样[表情]")

    def test_forwarded_mood_falls_back_to_repost_text(self) -> None:
        item = service._mood_item_from_sdk(
            {"tid": "t1", "uin": "123", "name": "小明", "content": "", "rt_content": "原文"}
        )
        assert item is not None
        self.assertEqual(item.content, "转发：原文")

    def test_mood_without_tid_is_dropped(self) -> None:
        self.assertIsNone(service._mood_item_from_sdk({"uin": "123", "content": "x"}))


class ActionResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_like_sends_expected_params(self) -> None:
        item = _feed_item()
        post = AsyncMock(return_value={"code": 0})
        with patch.object(service, "_post_cgi", post):
            await service.like(_FakeBot(), item)

        url, data = post.await_args.args[0], post.await_args.args[1]
        self.assertIn("internal_dolike_app", url)
        self.assertEqual(data["fid"], item.tid)
        self.assertEqual(data["unikey"], item.unikey)
        self.assertEqual(data["curkey"], item.curkey)
        self.assertEqual(data["abstime"], str(item.abstime))
        self.assertEqual(data["opuin"], "3946388948")

    async def test_like_raises_on_error_payload(self) -> None:
        post = AsyncMock(return_value={"code": -10000, "message": "操作太频繁"})
        with patch.object(service, "_post_cgi", post):
            with self.assertRaises(service.QzoneActionError) as ctx:
                await service.like(_FakeBot(), _feed_item())
        self.assertIn("操作太频繁", str(ctx.exception))

    async def test_comment_builds_topic_id(self) -> None:
        item = _feed_item(uin="3677697557", tid="abcd1234")
        post = AsyncMock(return_value={"code": 0})
        with patch.object(service, "_post_cgi", post):
            await service.comment(_FakeBot(), item, "好看")

        _, data = post.await_args.args[0], post.await_args.args[1]
        self.assertEqual(data["topicId"], "3677697557_abcd1234")
        self.assertEqual(data["hostUin"], "3677697557")
        self.assertEqual(data["content"], "好看")

    async def test_publish_returns_tid(self) -> None:
        run = AsyncMock(return_value={"success": True, "tid": "tid1", "curkey": "tid1"})
        with patch.object(service, "_run_sdk", run):
            result = await service.publish(_FakeBot(), "hello", visible=4)
        self.assertEqual(result, {"tid": "tid1", "curkey": "tid1"})

    async def test_publish_failure_becomes_action_error(self) -> None:
        run = AsyncMock(return_value={"success": False, "code": -3000, "message": "系统繁忙"})
        with patch.object(service, "_run_sdk", run):
            with self.assertRaises(service.QzoneActionError):
                await service.publish(_FakeBot(), "hello")

    async def test_fetch_feed_filters_ads(self) -> None:
        raw = [
            {"key": "k1", "uin": "1", "nickname": "a", "content": "hi", "appid": "311"},
            {"key": "advertisement_x", "uin": "0", "content": "ad"},
        ]
        with patch.object(service, "_run_sdk", AsyncMock(return_value=raw)):
            items = await service.fetch_feed(_FakeBot(), 5)
        self.assertEqual([item.tid for item in items], ["k1"])


class PayloadTests(unittest.TestCase):
    def test_jsonp_wrapped_payload_is_parsed(self) -> None:
        self.assertEqual(service._parse_payload('_Callback({"code":0});'), {"code": 0})

    def test_garbage_payload_returns_none(self) -> None:
        self.assertIsNone(service._parse_payload("<html>blocked</html>"))

    def test_ensure_ok_accepts_ret_zero(self) -> None:
        service._ensure_ok({"ret": 0}, "失败")

    def test_ensure_ok_raises_with_code(self) -> None:
        with self.assertRaises(service.QzoneActionError) as ctx:
            service._ensure_ok({"code": -10000}, "点赞失败")
        self.assertIn("-10000", str(ctx.exception))


class FeedCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        qzone._feed_cache.clear()

    def test_cached_item_can_be_picked_by_index(self) -> None:
        ctx = _FakeContext("")
        items = [_feed_item(tid="a"), _feed_item(tid="b")]
        qzone._store_feed(ctx, items)

        self.assertEqual(qzone._pick_cached_item("2", qzone._get_cached_feed(ctx)).tid, "b")  # type: ignore[union-attr]
        self.assertIsNone(qzone._pick_cached_item("3", qzone._get_cached_feed(ctx)))
        self.assertIsNone(qzone._pick_cached_item("abc", qzone._get_cached_feed(ctx)))

    def test_cache_is_per_session(self) -> None:
        qzone._store_feed(_FakeContext("", event=_FakeEvent(session_id="group:1")), [_feed_item()])
        self.assertIsNone(qzone._get_cached_feed(_FakeContext("", event=_FakeEvent(session_id="group:2"))))


class CommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        qzone._feed_cache.clear()
        self._config = {
            "enabled": True,
            "default_visible": "public",
            "max_length": 10,
            "feed_count": 8,
            "moods_count": 5,
            "timeout": 20,
            "permissions": {},
        }

    async def test_publish_command_sends_tid(self) -> None:
        ctx = _FakeContext("私密 你好")
        with patch.object(qzone, "get_config", return_value=self._config), patch.object(
            qzone.service, "publish", AsyncMock(return_value={"tid": "tid1"})
        ) as publish:
            await qzone.handle_publish(ctx)

        publish.assert_awaited_once()
        self.assertEqual(publish.await_args.kwargs["visible"], 4)
        self.assertIn("tid1", ctx.sent[-1])

    async def test_publish_command_rejects_too_long_text(self) -> None:
        ctx = _FakeContext("这条内容明显超过十个字了")
        with patch.object(qzone, "get_config", return_value=self._config), patch.object(
            qzone.service, "publish", AsyncMock()
        ) as publish:
            await qzone.handle_publish(ctx)

        publish.assert_not_awaited()
        self.assertIn("10", ctx.sent[-1])

    async def test_publish_command_requires_content(self) -> None:
        ctx = _FakeContext("私密")
        with patch.object(qzone, "get_config", return_value=self._config), patch.object(
            qzone.service, "publish", AsyncMock()
        ) as publish:
            await qzone.handle_publish(ctx)

        publish.assert_not_awaited()
        self.assertTrue(ctx.sent)

    async def test_feed_command_lists_items(self) -> None:
        ctx = _FakeContext("3")
        items = [_feed_item(nickname="小明", content="晚霞"), _feed_item(tid="b", nickname="小红")]
        with patch.object(qzone, "get_config", return_value=self._config), patch.object(
            qzone.service, "fetch_feed", AsyncMock(return_value=items)
        ):
            await qzone.handle_feed(ctx)

        text = ctx.sent[-1]
        self.assertIn("小明", text)
        self.assertIn("1.", text)
        self.assertEqual(qzone._pick_cached_item("1", qzone._get_cached_feed(ctx)).tid, items[0].tid)  # type: ignore[union-attr]

    async def test_like_command_without_cache_asks_for_feed(self) -> None:
        ctx = _FakeContext("1")
        with patch.object(qzone, "get_config", return_value=self._config), patch.object(
            qzone.service, "like", AsyncMock()
        ) as like:
            await qzone.handle_like(ctx)

        like.assert_not_awaited()
        self.assertTrue(ctx.sent)

    async def test_comment_command_uses_cached_item(self) -> None:
        qzone._store_feed(_FakeContext(""), [_feed_item(tid="abc")])
        ctx = _FakeContext("1 好看")
        with patch.object(qzone, "get_config", return_value=self._config), patch.object(
            qzone.service, "comment", AsyncMock()
        ) as comment:
            await qzone.handle_comment(ctx)

        comment.assert_awaited_once()
        self.assertEqual(comment.await_args.args[2], "好看")

    async def test_delete_command_by_tid(self) -> None:
        ctx = _FakeContext("d41d39eb3bb0dc6a34260e00")
        with patch.object(qzone, "get_config", return_value=self._config), patch.object(
            qzone.service, "delete", AsyncMock()
        ) as delete:
            await qzone.handle_delete(ctx)

        self.assertEqual(delete.await_args.args[1], "d41d39eb3bb0dc6a34260e00")


class AIToolTests(unittest.TestCase):
    def test_read_tool_registered(self) -> None:
        spec = get_ai_tool("qzone_get_feed")
        assert spec is not None
        self.assertTrue(spec.readonly)
        self.assertEqual(spec.plugin_name, "qzone")

    def test_publish_tool_is_gated(self) -> None:
        spec = get_ai_tool("qzone_publish")
        assert spec is not None
        self.assertFalse(spec.readonly)
        self.assertFalse(spec.enabled_by_default)
        self.assertTrue(spec.requires_superuser)


class AIToolHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_feed_returns_items(self) -> None:
        context = SimpleNamespace(bot=_FakeBot(), event=_FakeEvent())
        with patch.object(qzone, "get_config", return_value={"enabled": True, "timeout": 20}), patch.object(
            qzone.service, "fetch_feed", AsyncMock(return_value=[_feed_item()])
        ):
            result = await qzone.ai_tools.qzone_get_feed(context, {"count": 3})

        self.assertTrue(result["ok"])
        self.assertEqual(result["items"][0]["nickname"], "罗卜回")

    async def test_get_feed_without_bot_reports_error(self) -> None:
        with patch.object(qzone, "get_config", return_value={"enabled": True, "timeout": 20}):
            result = await qzone.ai_tools.qzone_get_feed(SimpleNamespace(bot=None, event=None), {})
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
