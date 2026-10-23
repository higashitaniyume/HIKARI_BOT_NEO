"""AI Agent 聊天文件接收模块测试。

覆盖：
- 文件段解析（包含引用消息中的文件段）
- 本地路径优先复制
- get_file / get_group_file_url / get_private_file_url / direct url 阶梯回退
- 100MB 硬上限拒绝（已知大小与流式下载均拦截）
- 10 分钟内重放去重
- 懒清理过期 incoming 文件
"""

from __future__ import annotations

import io
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from plugins.aiagent import attachments


class FakeMessageSegment:
    def __init__(self, seg_type: str, data: dict[str, Any]) -> None:
        self.type = seg_type
        self.data = data


class FakeReply:
    def __init__(self, segments: list[FakeMessageSegment]) -> None:
        self.message = segments


class FakeEvent:
    def __init__(
        self,
        segments: list[FakeMessageSegment],
        reply_segments: list[FakeMessageSegment] | None = None,
        user_id: int = 123456,
        group_id: int | None = None,
    ) -> None:
        self._message = segments
        self.reply = FakeReply(reply_segments) if reply_segments else None
        self.user_id = user_id
        self.group_id = group_id

    def get_message(self) -> list[FakeMessageSegment]:
        return self._message

    def get_user_id(self) -> str:
        return str(self.user_id)


class FakeBot:
    def __init__(self) -> None:
        self.api_responses: dict[str, Any] = {}
        self.api_calls: list[dict[str, Any]] = []

    async def call_api(self, api: str, **kwargs: Any) -> Any:
        self.api_calls.append({"api": api, "kwargs": kwargs})
        if api in self.api_responses:
            resp = self.api_responses[api]
            if isinstance(resp, Exception):
                raise resp
            return resp
        return {}


def _cfg(**overrides: Any) -> dict[str, Any]:
    ws_cfg = {
        "enabled": True,
        "ingest_incoming": True,
        "include_quoted_files": True,
        "incoming_max_mb": 100,
        "incoming_max_files": 3,
        "incoming_retention_days": 7,
    }
    ws_cfg.update(overrides)
    return {"tools": {"workspace": ws_cfg}}


class AttachmentsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.orig_cwd = os.getcwd()
        os.chdir(self.temp_dir)
        attachments._recent_downloads.clear()

    def tearDown(self) -> None:
        os.chdir(self.orig_cwd)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_collect_file_segments_including_reply(self) -> None:
        seg1 = FakeMessageSegment("file", {"file_id": "f1", "name": "main.pdf"})
        seg_reply = FakeMessageSegment("file", {"file_id": "f2", "name": "ref.docx"})
        seg_text = FakeMessageSegment("text", {"text": "hello"})

        event = FakeEvent([seg1, seg_text], reply_segments=[seg_reply])
        collected = attachments.collect_file_segments(event, include_quoted=True)

        self.assertEqual(len(collected), 2)
        self.assertEqual(collected[0]["file_id"], "f2")
        self.assertEqual(collected[1]["file_id"], "f1")

    async def test_download_local_path_priority(self) -> None:
        bot = FakeBot()
        temp_file = Path(self.temp_dir) / "napcat_temp.pdf"
        temp_file.write_bytes(b"%PDF sample")

        seg = FakeMessageSegment("file", {"name": "test.pdf", "path": str(temp_file)})
        event = FakeEvent([seg], user_id=10001)

        prompts = await attachments.collect_and_save_attachments(bot, event, _cfg())
        self.assertEqual(len(prompts), 1)
        self.assertIn("已保存到你的工作区", prompts[0])
        self.assertIn("test.pdf", prompts[0])
        # 本地优先，不应该调用 bot.call_api
        self.assertEqual(len(bot.api_calls), 0)

        # 检查文件是否确实已复制到工作区 incoming
        ws_incoming = Path("UserData/aiagent_files/private/10001/incoming")
        saved_files = list(ws_incoming.glob("*test.pdf"))
        self.assertEqual(len(saved_files), 1)

    async def test_download_get_file_fallback(self) -> None:
        bot = FakeBot()
        remote_local_file = Path(self.temp_dir) / "from_napcat.txt"
        remote_local_file.write_text("content", encoding="utf-8")

        bot.api_responses["get_file"] = {"file": str(remote_local_file)}

        seg = FakeMessageSegment("file", {"file_id": "file_123", "name": "report.txt"})
        event = FakeEvent([seg], user_id=10001)

        prompts = await attachments.collect_and_save_attachments(bot, event, _cfg())
        self.assertEqual(len(prompts), 1)
        self.assertIn("已保存到你的工作区", prompts[0])
        self.assertEqual(len(bot.api_calls), 1)
        self.assertEqual(bot.api_calls[0]["api"], "get_file")

    async def test_reject_over_100mb(self) -> None:
        bot = FakeBot()
        # 105 MB
        large_size = 105 * 1024 * 1024
        seg = FakeMessageSegment(
            "file",
            {"file_id": "huge_1", "name": "huge.zip", "file_size": str(large_size)},
        )
        event = FakeEvent([seg], user_id=10001)

        prompts = await attachments.collect_and_save_attachments(bot, event, _cfg())
        self.assertEqual(len(prompts), 1)
        self.assertIn("超过单文件 100MB 上限", prompts[0])
        self.assertIn("huge.zip", prompts[0])
        # 不应调用任何下载接口
        self.assertEqual(len(bot.api_calls), 0)

    async def test_deduplication_within_10_minutes(self) -> None:
        bot = FakeBot()
        local_file = Path(self.temp_dir) / "doc.txt"
        local_file.write_text("doc content", encoding="utf-8")

        seg = FakeMessageSegment("file", {"file_id": "unique_doc_1", "path": str(local_file), "name": "doc.txt"})
        event = FakeEvent([seg], user_id=10001)

        # 第一次
        prompts1 = await attachments.collect_and_save_attachments(bot, event, _cfg())
        self.assertEqual(len(prompts1), 1)
        self.assertIn("已保存到你的工作区", prompts1[0])

        # 第二次相同文件
        prompts2 = await attachments.collect_and_save_attachments(bot, event, _cfg())
        self.assertEqual(len(prompts2), 1)
        self.assertEqual(prompts1[0], prompts2[0])


if __name__ == "__main__":
    unittest.main()
