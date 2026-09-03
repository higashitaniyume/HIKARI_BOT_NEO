"""AI Agent 图片输入（vision）测试。

覆盖: vision 配置钳制、图片直链收集（引用消息 + 当前消息）、字节头识别格式、
下载转 base64 data URI、块数组文本提取与降级、Responses API 块转换、
以及模型不支持图片时的 400 去图重试。
"""

from __future__ import annotations

import base64
import unittest
from typing import Any
from unittest.mock import patch

import plugins.aiagent as aiagent
from plugins.aiagent import client as aiagent_client
from plugins.aiagent import responses_client as responses_client
from plugins.aiagent import vision
from plugins.aiagent.wiki import _latest_user_text

JPEG = b"\xff\xd8\xff" + b"jpeg-body"
PNG = b"\x89PNG\r\n\x1a\n" + b"png-body"
GIF = b"GIF89a" + b"gif-body"
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"webp-body"

_PLACEHOLDER_IMAGE = {
    "type": "image_url",
    "image_url": {"url": "data:image/png;base64,AAA", "detail": "low"},
}


class FakeSegment:
    def __init__(self, type_: str, data: dict[str, Any]) -> None:
        self.type = type_
        self.data = data


class FakeReply:
    def __init__(self, message: list[FakeSegment]) -> None:
        self.message = message


class FakeEvent:
    def __init__(self, message: list[FakeSegment], reply: FakeReply | None = None) -> None:
        self._message = message
        self.reply = reply

    def get_message(self) -> list[FakeSegment]:
        return self._message


class FakeStreamResponse:
    def __init__(self, status_code: int, payload: bytes) -> None:
        self.status_code = status_code
        self._payload = payload

    async def aiter_bytes(self):
        for index in range(0, len(self._payload), 8):
            yield self._payload[index : index + 8]


class FakeStreamContext:
    def __init__(self, response: FakeStreamResponse) -> None:
        self._response = response

    async def __aenter__(self) -> FakeStreamResponse:
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class DownloadAsyncClient:
    responses: dict[str, tuple[int, bytes]] = {}
    requested: list[str] = []

    def __init__(self, *args, **kwargs) -> None:
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def stream(self, method: str, url: str) -> FakeStreamContext:
        DownloadAsyncClient.requested.append(url)
        status, payload = DownloadAsyncClient.responses.get(url, (404, b""))
        return FakeStreamContext(FakeStreamResponse(status, payload))


class FakeResponse:
    def __init__(self, status_code: int, data: dict[str, object], text: str = "") -> None:
        self.status_code = status_code
        self._data = data
        self.text = text

    def json(self) -> dict[str, object]:
        return self._data


class VisionUnsupportedChatAsyncClient:
    """第 1 轮因图片被拒 400，第 2 轮（已去图）成功。"""

    post_payloads: list[dict[str, object]] = []

    def __init__(self, *args, **kwargs) -> None:
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, object]):
        VisionUnsupportedChatAsyncClient.post_payloads.append(json)
        if len(VisionUnsupportedChatAsyncClient.post_payloads) == 1:
            return FakeResponse(400, {}, "This model does not support image")
        return FakeResponse(200, {"choices": [{"message": {"role": "assistant", "content": "纯文本回复"}}]})


class VisionUnsupportedResponsesAsyncClient:
    post_payloads: list[dict[str, object]] = []

    def __init__(self, *args, **kwargs) -> None:
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, object]):
        VisionUnsupportedResponsesAsyncClient.post_payloads.append(json)
        if len(VisionUnsupportedResponsesAsyncClient.post_payloads) == 1:
            return FakeResponse(400, {}, "This model does not support image")
        return FakeResponse(
            200,
            {
                "output": [
                    {
                        "type": "message",
                        "id": "msg_1",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "纯文本回复"}],
                    }
                ],
                "output_text": "纯文本回复",
            },
        )


def vision_config(**overrides: Any) -> dict[str, Any]:
    section: dict[str, Any] = {
        "enabled": True,
        "max_images": 2,
        "detail": "low",
        "include_quoted": True,
        "max_bytes": 65536,
        "download_timeout_seconds": 20,
    }
    section.update(overrides)
    return {"vision": section}


def _model_cfg(base_url: str) -> dict[str, object]:
    return {
        "base_url": base_url,
        "api_key": "",
        "model": "test-vision-model",
        "temperature": 0.7,
        "top_p": 1.0,
        "max_tokens": 256,
        "timeout_seconds": 5,
        "proxy": "",
    }


def chat_cfg() -> dict[str, object]:
    return {
        "api": {"protocol": "chat_completions"},
        "model": _model_cfg("https://api.example.test/v1"),
        "thinking": {"enabled": False},
        "tools": {
            "search": {"enabled": True, "base_url": "http://searxng-core:8080"},
            "max_tool_rounds": 2,
        },
    }


def responses_cfg() -> dict[str, object]:
    return {
        "api": {"protocol": "responses"},
        "model": _model_cfg("https://api.deepseek.com"),
        "thinking": {"enabled": False},
        "tools": {
            "search": {"enabled": True, "mode": "builtin", "base_url": "http://searxng-core:8080"},
            "max_tool_rounds": 2,
        },
    }


class VisionConfigTests(unittest.TestCase):
    def test_defaults_when_section_missing(self) -> None:
        settings = vision.vision_cfg({})
        self.assertFalse(settings["enabled"])
        self.assertEqual(settings["max_images"], 2)
        self.assertEqual(settings["detail"], "low")
        self.assertTrue(settings["include_quoted"])
        self.assertEqual(settings["max_bytes"], 5_242_880)
        self.assertEqual(settings["download_timeout_seconds"], 20)

    def test_out_of_range_values_are_clamped(self) -> None:
        settings = vision.vision_cfg(
            vision_config(max_images=99, max_bytes=10, download_timeout_seconds=9999)
        )
        self.assertEqual(settings["max_images"], 8)
        self.assertEqual(settings["max_bytes"], 65536)
        self.assertEqual(settings["download_timeout_seconds"], 120)

    def test_detail_is_validated(self) -> None:
        for detail in vision.VISION_DETAILS:
            self.assertEqual(vision.vision_cfg(vision_config(detail=detail))["detail"], detail)
        self.assertEqual(vision.vision_cfg(vision_config(detail=" HIGH "))["detail"], "high")
        # 非法值回落到 low（最省钱的档位）
        self.assertEqual(vision.vision_cfg(vision_config(detail="ultra"))["detail"], "low")
        self.assertEqual(vision.vision_cfg(vision_config(detail=""))["detail"], "low")


class CollectImageUrlsTests(unittest.TestCase):
    def test_disabled_collects_nothing(self) -> None:
        event = FakeEvent([FakeSegment("image", {"url": "https://img.test/a.jpg"})])
        self.assertEqual(vision.collect_image_urls(event, vision_config(enabled=False)), [])

    def test_quoted_images_come_before_current_message(self) -> None:
        event = FakeEvent(
            [
                FakeSegment("text", {"text": "这是什么"}),
                FakeSegment("image", {"url": "https://img.test/b.jpg"}),
            ],
            FakeReply([FakeSegment("image", {"url": "https://img.test/a.jpg"})]),
        )
        self.assertEqual(
            vision.collect_image_urls(event, vision_config()),
            ["https://img.test/a.jpg", "https://img.test/b.jpg"],
        )

    def test_quoted_images_skipped_when_include_quoted_off(self) -> None:
        event = FakeEvent(
            [FakeSegment("image", {"url": "https://img.test/b.jpg"})],
            FakeReply([FakeSegment("image", {"url": "https://img.test/a.jpg"})]),
        )
        self.assertEqual(
            vision.collect_image_urls(event, vision_config(include_quoted=False)),
            ["https://img.test/b.jpg"],
        )

    def test_duplicates_removed_and_capped_at_max_images(self) -> None:
        segments = [FakeSegment("image", {"url": f"https://img.test/{i}.jpg"}) for i in range(4)]
        segments.append(FakeSegment("image", {"url": "https://img.test/0.jpg"}))
        self.assertEqual(
            vision.collect_image_urls(FakeEvent(segments), vision_config(max_images=3)),
            ["https://img.test/0.jpg", "https://img.test/1.jpg", "https://img.test/2.jpg"],
        )

    def test_file_field_used_when_url_missing(self) -> None:
        event = FakeEvent([FakeSegment("image", {"file": "https://img.test/c.jpg"})])
        self.assertEqual(vision.collect_image_urls(event, vision_config()), ["https://img.test/c.jpg"])

    def test_non_http_sources_are_ignored(self) -> None:
        event = FakeEvent(
            [
                FakeSegment("image", {"file": "C:/temp/local.jpg"}),
                FakeSegment("image", {"file": "abc123.image"}),
                FakeSegment("text", {"text": "https://img.test/not-an-image.jpg"}),
            ]
        )
        self.assertEqual(vision.collect_image_urls(event, vision_config()), [])


class SniffMimeTests(unittest.TestCase):
    def test_known_signatures(self) -> None:
        self.assertEqual(vision._sniff_mime(JPEG), "image/jpeg")
        self.assertEqual(vision._sniff_mime(PNG), "image/png")
        self.assertEqual(vision._sniff_mime(GIF), "image/gif")
        self.assertEqual(vision._sniff_mime(b"GIF87a-old"), "image/gif")
        self.assertEqual(vision._sniff_mime(WEBP), "image/webp")

    def test_unknown_bytes_return_empty(self) -> None:
        self.assertEqual(vision._sniff_mime(b"definitely not an image"), "")
        self.assertEqual(vision._sniff_mime(b""), "")


class BuildImageBlocksTests(unittest.IsolatedAsyncioTestCase):
    async def test_downloads_become_data_uri_blocks(self) -> None:
        DownloadAsyncClient.requested = []
        DownloadAsyncClient.responses = {
            "https://img.test/a.jpg": (200, JPEG),
            "https://img.test/b.png": (200, PNG),
        }
        with patch.object(vision.httpx, "AsyncClient", DownloadAsyncClient):
            blocks = await vision.build_image_blocks(
                ["https://img.test/a.jpg", "https://img.test/b.png"], vision_config(detail="high")
            )

        self.assertEqual(
            blocks,
            [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{base64.b64encode(JPEG).decode('ascii')}",
                        "detail": "high",
                    },
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{base64.b64encode(PNG).decode('ascii')}",
                        "detail": "high",
                    },
                },
            ],
        )
        self.assertEqual(DownloadAsyncClient.requested, ["https://img.test/a.jpg", "https://img.test/b.png"])

    async def test_oversized_missing_and_unsupported_images_are_dropped(self) -> None:
        DownloadAsyncClient.requested = []
        DownloadAsyncClient.responses = {
            "https://img.test/big.jpg": (200, b"\xff\xd8\xff" + b"a" * 70000),
            "https://img.test/text.bin": (200, b"definitely not an image"),
            "https://img.test/missing.jpg": (404, b""),
            "https://img.test/ok.gif": (200, GIF),
        }
        urls = [
            "https://img.test/big.jpg",
            "https://img.test/text.bin",
            "https://img.test/missing.jpg",
            "https://img.test/ok.gif",
        ]
        with (
            patch.object(vision.httpx, "AsyncClient", DownloadAsyncClient),
            patch.object(vision.logger, "warning"),
        ):
            blocks = await vision.build_image_blocks(urls, vision_config(max_images=4, max_bytes=65536))

        self.assertEqual(len(blocks), 1)
        self.assertTrue(str(blocks[0]["image_url"]["url"]).startswith("data:image/gif;base64,"))

    async def test_empty_url_list_skips_download(self) -> None:
        DownloadAsyncClient.requested = []
        with patch.object(vision.httpx, "AsyncClient", DownloadAsyncClient):
            self.assertEqual(await vision.build_image_blocks([], vision_config()), [])
        self.assertEqual(DownloadAsyncClient.requested, [])


class BlockContentTests(unittest.TestCase):
    def test_content_text_reads_str_and_block_arrays(self) -> None:
        self.assertEqual(vision.content_text("纯文本"), "纯文本")
        self.assertEqual(
            vision.content_text(
                [
                    {"type": "text", "text": "第一段"},
                    _PLACEHOLDER_IMAGE,
                    {"type": "input_text", "text": "第二段"},
                ]
            ),
            "第一段\n第二段",
        )
        self.assertEqual(vision.content_text(None), "")

    def test_strip_images_flattens_without_mutating_input(self) -> None:
        messages = [
            {"role": "system", "content": "指令"},
            {"role": "user", "content": [vision.text_block("看图"), _PLACEHOLDER_IMAGE]},
        ]
        self.assertTrue(vision.has_images(messages))

        stripped = vision.strip_images(messages)
        self.assertFalse(vision.has_images(stripped))
        self.assertEqual(stripped[1]["content"], "看图")
        self.assertEqual(stripped[0], {"role": "system", "content": "指令"})
        self.assertIsInstance(messages[1]["content"], list)

    def test_latest_user_text_reads_block_arrays(self) -> None:
        # 工具预取和 _tool_wanted 都依赖这个函数，带图消息不能让它返回空
        text = _latest_user_text(
            [
                {"role": "system", "content": "指令"},
                {"role": "user", "content": [vision.text_block("这张图是什么"), _PLACEHOLDER_IMAGE]},
            ]
        )
        self.assertEqual(text, "这张图是什么")


class ResponsesBlockConversionTests(unittest.TestCase):
    def test_image_block_is_flattened_with_top_level_detail(self) -> None:
        parts = responses_client._input_parts(
            [
                {"type": "text", "text": "这张图是什么"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA", "detail": "low"}},
            ]
        )
        self.assertEqual(
            parts,
            [
                {"type": "input_text", "text": "这张图是什么"},
                {"type": "input_image", "image_url": "data:image/jpeg;base64,AAA", "detail": "low"},
            ],
        )

    def test_input_image_block_passes_through(self) -> None:
        parts = responses_client._input_parts(
            [{"type": "input_image", "image_url": "data:image/png;base64,AAA"}]
        )
        self.assertEqual(parts, [{"type": "input_image", "image_url": "data:image/png;base64,AAA"}])

    def test_blocks_without_url_are_dropped(self) -> None:
        parts = responses_client._input_parts(
            [{"type": "image_url", "image_url": {}}, {"type": "audio"}, "not-a-block"]
        )
        self.assertEqual(parts, [])

    def test_message_item_converts_block_content(self) -> None:
        item = responses_client._message_item(
            {"role": "user", "content": [vision.text_block("看图"), _PLACEHOLDER_IMAGE]}
        )
        self.assertEqual(
            item,
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "看图"},
                    {"type": "input_image", "image_url": "data:image/png;base64,AAA", "detail": "low"},
                ],
            },
        )


class BuildMessagesTests(unittest.TestCase):
    def _messages(self, image_blocks: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        with (
            patch.object(aiagent, "load_persona_prompt", return_value="人格"),
            patch.object(aiagent, "read_memory_context", return_value=""),
            patch.object(aiagent, "get_history", return_value=[]),
        ):
            return aiagent._build_messages({}, FakeEvent([]), "group_1", "这张图是什么", image_blocks)

    def test_image_blocks_attach_to_final_user_message(self) -> None:
        messages = self._messages([_PLACEHOLDER_IMAGE])
        self.assertEqual(messages[-1]["role"], "user")
        self.assertEqual(
            messages[-1]["content"], [{"type": "text", "text": "这张图是什么"}, _PLACEHOLDER_IMAGE]
        )
        # 图片只能挂在 user 消息上
        self.assertTrue(all(isinstance(m["content"], str) for m in messages[:-1]))

    def test_text_only_keeps_plain_string_content(self) -> None:
        messages = self._messages(None)
        self.assertEqual(messages[-1]["content"], "这张图是什么")
        self.assertFalse(vision.has_images(messages))


class VisionDegradationTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _messages_with_image() -> list[dict[str, Any]]:
        return [
            {"role": "system", "content": "你是测试助手。"},
            {"role": "user", "content": [vision.text_block("这张图是什么"), _PLACEHOLDER_IMAGE]},
        ]

    async def test_chat_completions_retries_without_images(self) -> None:
        VisionUnsupportedChatAsyncClient.post_payloads = []
        with (
            patch.object(aiagent_client.httpx, "AsyncClient", VisionUnsupportedChatAsyncClient),
            patch.object(aiagent_client.logger, "warning"),
        ):
            reply = await aiagent_client.request_chat_completion(chat_cfg(), self._messages_with_image())

        self.assertEqual(reply, "纯文本回复")
        payloads = VisionUnsupportedChatAsyncClient.post_payloads
        self.assertEqual(len(payloads), 2)
        self.assertIsInstance(payloads[0]["messages"][-1]["content"], list)
        self.assertEqual(payloads[1]["messages"][-1]["content"], "这张图是什么")
        # 去图降级先于 tools 降级：第 2 轮仍然带着工具
        self.assertIn("tools", payloads[1])

    async def test_responses_api_retries_without_images(self) -> None:
        VisionUnsupportedResponsesAsyncClient.post_payloads = []
        with (
            patch.object(responses_client.httpx, "AsyncClient", VisionUnsupportedResponsesAsyncClient),
            patch.object(responses_client.logger, "warning"),
        ):
            reply = await responses_client.request_response_completion(
                responses_cfg(), self._messages_with_image()
            )

        self.assertEqual(reply, "纯文本回复")
        payloads = VisionUnsupportedResponsesAsyncClient.post_payloads
        self.assertEqual(len(payloads), 2)
        self.assertIsInstance(payloads[0]["input"][-1]["content"], list)
        self.assertEqual(payloads[1]["input"][-1]["content"], "这张图是什么")
        self.assertIn("tools", payloads[1])


if __name__ == "__main__":
    unittest.main()
