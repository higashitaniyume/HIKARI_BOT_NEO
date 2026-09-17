"""发送策略回归：多个媒体一律合并转发（含游戏信息），合并失败就什么都不发。

生产实例（2026-09-17 22:10）：Steam 页面 7 个预告片走合并转发，90s 被误判失败后逐条
补发 7 个视频，而 NapCat 仍在后台上传、约 2 分钟后把聊天记录也发了出去，用户收到两遍。
现在的规则：转发超时给足（默认 300s）、失败不回退逐条发送、宁可什么都不发。
"""

import copy
import unittest

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from core.defaults import DEFAULT_MEDIA_PARSER_CONFIG
from plugins.media_parser.sender import _chunk_media_messages, send_metadata_result

BASE_CONFIG = {
    "max_send": 8,
    "message": {"text_metadata": {"max_desc_chars": 600, "show_url": True}},
    "send_strategy": {
        "prefer_forward_message": True,
        "fallback_to_separate_media": False,
        "include_text_in_forward": True,
        "forward_timeout_seconds": 300,
    },
}


def _metadata(videos: int, images: int) -> dict:
    return {
        "platform": "steam",
        "source_url": "https://store.steampowered.com/app/2914150/_/",
        "title": "测试游戏",
        "_enable_text_metadata": True,
        "_enable_rich_media": True,
        "video_urls": [[f"https://cdn.example/video_{index}.mp4"] for index in range(videos)],
        "image_urls": [[f"https://cdn.example/image_{index}.jpg"] for index in range(images)],
        "video_modes": ["direct"] * videos,
        "image_modes": ["direct"] * images,
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
            import asyncio

            await asyncio.sleep(5)
        elif self.forward == "rejected":
            raise RuntimeError("retcode=1200 发送伪造合并转发消息失败")
        self.forwards.append(messages)

    async def send_group_forward_msg(
        self,
        group_id: int,
        messages: list[MessageSegment],
        **kwargs: object,
    ) -> None:
        raise AssertionError("私聊场景不应走群合并转发")


def _node_text(node: MessageSegment) -> str:
    return str(node.data["content"])


class MergeEverythingTests(unittest.IsolatedAsyncioTestCase):
    async def test_videos_and_images_go_into_one_forward(self) -> None:
        """视频也在合并转发里：不再因为「含视频」改成逐条发送。"""
        bot = _FakeBot()

        sent = await send_metadata_result(bot, _FakeEvent(), _metadata(videos=2, images=2), copy.deepcopy(BASE_CONFIG))

        self.assertEqual(4, sent)
        self.assertEqual(1, len(bot.forwards))
        self.assertEqual([], bot.sent)
        # 首条是游戏信息文本，其余四条是媒体
        self.assertEqual(5, len(bot.forwards[0]))

    async def test_game_info_rides_as_first_node(self) -> None:
        bot = _FakeBot()

        await send_metadata_result(bot, _FakeEvent(), _metadata(videos=2, images=0), copy.deepcopy(BASE_CONFIG))

        nodes = bot.forwards[0]
        self.assertEqual(3, len(nodes))
        self.assertIn("测试游戏", _node_text(nodes[0]))

    async def test_single_media_is_sent_directly(self) -> None:
        """单个媒体没什么可合并的，直接发送（文本照常先发）。"""
        bot = _FakeBot()

        sent = await send_metadata_result(bot, _FakeEvent(), _metadata(videos=1, images=0), copy.deepcopy(BASE_CONFIG))

        self.assertEqual(1, sent)
        self.assertEqual([], bot.forwards)
        self.assertEqual(2, len(bot.sent))


class MergeFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejected_forward_sends_nothing(self) -> None:
        """合并转发被 NapCat 拒绝时不逐条补发，整条链接什么都不发。"""
        bot = _FakeBot(forward="rejected")

        sent = await send_metadata_result(bot, _FakeEvent(), _metadata(videos=2, images=2), copy.deepcopy(BASE_CONFIG))

        self.assertEqual(0, sent)
        self.assertEqual([], bot.sent)
        self.assertEqual([], bot.forwards)

    async def test_forward_timeout_sends_nothing(self) -> None:
        bot = _FakeBot(forward="timeout")
        config = copy.deepcopy(BASE_CONFIG)
        config["send_strategy"]["forward_timeout_seconds"] = 1

        sent = await send_metadata_result(bot, _FakeEvent(), _metadata(videos=2, images=2), config)

        self.assertEqual(0, sent)
        self.assertEqual([], bot.sent)

    async def test_fallback_only_when_explicitly_enabled(self) -> None:
        """仍然保留开关：显式打开才逐条补发。"""
        bot = _FakeBot(forward="rejected")
        config = copy.deepcopy(BASE_CONFIG)
        config["send_strategy"]["fallback_to_separate_media"] = True

        sent = await send_metadata_result(bot, _FakeEvent(), _metadata(videos=2, images=0), config)

        self.assertEqual(2, sent)
        # 逐条回退时游戏信息文本也会单独发出
        self.assertEqual(3, len(bot.sent))


class SendStrategyDefaultTests(unittest.TestCase):
    def test_defaults_never_fall_back_and_wait_long(self) -> None:
        strategy = DEFAULT_MEDIA_PARSER_CONFIG["send_strategy"]

        self.assertTrue(strategy["prefer_forward_message"])
        self.assertTrue(strategy["include_text_in_forward"])
        self.assertIs(False, strategy["fallback_to_separate_media"])
        self.assertEqual(300, strategy["forward_timeout_seconds"])


class ChunkHelperTests(unittest.TestCase):
    def test_chunk_media_messages_splits_by_size(self) -> None:
        media = [
            ("image", Message(MessageSegment.image(f"https://cdn.example/{index}.jpg")))
            for index in range(5)
        ]

        chunks = _chunk_media_messages(media, 2)

        self.assertEqual([2, 2, 1], [len(chunk) for chunk in chunks])


if __name__ == "__main__":
    unittest.main()
