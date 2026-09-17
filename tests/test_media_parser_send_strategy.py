"""发送策略回归：视频不走合并转发，合并转发超时不重复补发。

生产实例（群 165178207 / 私聊，2026-09-17 22:10）：Steam 页面解析出 7 个预告片，
合并转发在 90s 时被判超时 → 逐条补发 7 个视频，而 NapCat 其实还在后台上传，
约 2 分钟后把聊天记录也发了出去，用户于是收到两遍。
"""

import asyncio
import copy
import unittest

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from plugins.media_parser.sender import _has_video_media, send_metadata_result

BASE_CONFIG = {
    "max_send": 8,
    "message": {},
    "send_strategy": {
        "prefer_forward_message": True,
        "fallback_to_separate_media": True,
        "include_text_in_forward": True,
        "forward_timeout_seconds": 90,
    },
}


def _video_metadata(count: int) -> dict:
    return {
        "platform": "steam",
        "source_url": "https://store.steampowered.com/app/2914150/_/",
        "title": "测试游戏",
        "_enable_text_metadata": False,
        "_enable_rich_media": True,
        "video_urls": [[f"https://cdn.example/video_{index}.mp4"] for index in range(count)],
        "image_urls": [],
        "video_modes": ["direct"] * count,
        "image_modes": [],
    }


def _image_metadata(count: int) -> dict:
    return {
        "platform": "steam",
        "source_url": "https://store.steampowered.com/app/2914150/_/",
        "title": "测试游戏",
        "_enable_text_metadata": False,
        "_enable_rich_media": True,
        "video_urls": [],
        "image_urls": [[f"https://cdn.example/image_{index}.jpg"] for index in range(count)],
        "video_modes": [],
        "image_modes": ["direct"] * count,
    }


class _FakeEvent:
    def get_user_id(self) -> str:
        return "3433559280"


class _FakeBot:
    """只记录发送行为的 Bot 替身。"""

    self_id = "3946388948"

    def __init__(self, forward: str = "ok") -> None:
        self.forward = forward
        self.sent: list[Message] = []
        self.forwards: list[list[MessageSegment]] = []

    async def send(self, event: object, message: Message) -> None:
        self.sent.append(message)

    async def send_private_forward_msg(
        self,
        user_id: int,
        messages: list[MessageSegment],
        **kwargs: object,
    ) -> None:
        if self.forward == "timeout":
            await asyncio.sleep(5)
        elif self.forward == "error":
            raise RuntimeError("retcode=1200 发送伪造合并转发消息失败")
        self.forwards.append(messages)

    async def send_group_forward_msg(
        self,
        group_id: int,
        messages: list[MessageSegment],
        **kwargs: object,
    ) -> None:
        raise AssertionError("私聊场景不应走群合并转发")


class VideoBypassForwardTests(unittest.IsolatedAsyncioTestCase):
    async def test_video_media_skips_merged_forward(self) -> None:
        bot = _FakeBot()

        sent = await send_metadata_result(bot, _FakeEvent(), _video_metadata(2), copy.deepcopy(BASE_CONFIG))

        self.assertEqual(2, sent)
        self.assertEqual([], bot.forwards)
        self.assertEqual(2, len(bot.sent))

    async def test_image_only_still_uses_merged_forward(self) -> None:
        bot = _FakeBot()

        sent = await send_metadata_result(bot, _FakeEvent(), _image_metadata(3), copy.deepcopy(BASE_CONFIG))

        self.assertEqual(3, sent)
        self.assertEqual(1, len(bot.forwards))
        self.assertEqual([], bot.sent)

    async def test_has_video_media_detects_video(self) -> None:
        video_meta = _video_metadata(1)
        image_meta = _image_metadata(1)

        from plugins.media_parser.sender import build_media_messages

        self.assertTrue(_has_video_media(build_media_messages(video_meta, max_send=8)))
        self.assertFalse(_has_video_media(build_media_messages(image_meta, max_send=8)))


class ForwardTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_does_not_resend_separately(self) -> None:
        """超时只代表「还没返回」，再补发就会重复——按已送达处理，不再逐条发送。"""
        bot = _FakeBot(forward="timeout")
        config = copy.deepcopy(BASE_CONFIG)
        config["send_strategy"]["forward_timeout_seconds"] = 0.05

        sent = await send_metadata_result(bot, _FakeEvent(), _image_metadata(3), config)

        self.assertEqual(3, sent)
        self.assertEqual([], bot.sent)

    async def test_explicit_failure_still_falls_back(self) -> None:
        """明确报错（不是超时）时仍然逐条补发。"""
        bot = _FakeBot(forward="error")

        sent = await send_metadata_result(bot, _FakeEvent(), _image_metadata(2), copy.deepcopy(BASE_CONFIG))

        self.assertEqual(2, sent)
        self.assertEqual(2, len(bot.sent))


if __name__ == "__main__":
    unittest.main()
