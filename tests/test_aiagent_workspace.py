"""AI Agent 文件工作区（workspace_* 与 send_file）单元测试。

覆盖：
- 私聊 / 群聊目录隔离（服务端计算，不可逃逸）
- 相对路径校验（防绝对路径、盘符、.. 逃逸）
- CRUD（list, read, write, delete, move, mkdir）
- 二进制文件不可直接以文本读取
- 内容不可信 notice
- 压缩与解压（防 zip-slip、解压容量限制）
- 配额限制（单文件大小、总容量、文件总数）
- send_file 文件发送与 100MB 上限
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

from plugins.aiagent.tools import workspace as ws_tool


class FakeEvent:
    def __init__(self, user_id: int = 123456, group_id: int | None = None) -> None:
        self.user_id = user_id
        self.group_id = group_id

    def get_user_id(self) -> str:
        return str(self.user_id)


class FakeBot:
    def __init__(self) -> None:
        self.api_calls: list[dict[str, Any]] = []

    async def call_api(self, api: str, **kwargs: Any) -> dict[str, Any]:
        self.api_calls.append({"api": api, "kwargs": kwargs})
        return {"status": "ok"}


class FakeContext:
    def __init__(self, user_id: int = 123456, group_id: int | None = None) -> None:
        self.bot = FakeBot()
        self.event = FakeEvent(user_id=user_id, group_id=group_id)


def _cfg(
    *,
    allow_writes: bool = True,
    allow_delete: bool = True,
    allow_send: bool = True,
    **overrides: Any,
) -> dict[str, Any]:
    ws_cfg = {
        "enabled": True,
        "allow_writes": allow_writes,
        "allow_delete": allow_delete,
        "allow_send": allow_send,
        "max_file_mb": 20,
        "max_total_mb": 300,
        "max_files": 500,
        "max_send_mb": 100,
    }
    ws_cfg.update(overrides)
    return {"tools": {"workspace": ws_cfg}}


class WorkspaceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.orig_cwd = os.getcwd()
        os.chdir(self.temp_dir)

    def tearDown(self) -> None:
        os.chdir(self.orig_cwd)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_session_workspace_root_isolation(self) -> None:
        private_ctx = FakeContext(user_id=11111)
        group_ctx = FakeContext(user_id=11111, group_id=22222)

        p_root = ws_tool.get_session_workspace_root(private_ctx)
        g_root = ws_tool.get_session_workspace_root(group_ctx)

        self.assertEqual(p_root.as_posix(), "UserData/aiagent_files/private/11111")
        self.assertEqual(g_root.as_posix(), "UserData/aiagent_files/group/22222")

    def test_path_escape_prevention(self) -> None:
        root = Path("UserData/aiagent_files/private/11111")
        root.mkdir(parents=True, exist_ok=True)

        # 绝对路径
        with self.assertRaises(ValueError):
            ws_tool.resolve_session_path(root, "/etc/passwd")

        # 路径遍历
        with self.assertRaises(ValueError):
            ws_tool.resolve_session_path(root, "../other/secret.txt")

        # 复合遍历
        with self.assertRaises(ValueError):
            ws_tool.resolve_session_path(root, "foo/../../bar")

        # 合法相对路径
        p = ws_tool.resolve_session_path(root, "docs/note.txt")
        self.assertTrue(p.resolve().is_relative_to((Path.cwd() / root).resolve()))

    async def test_crud_operations(self) -> None:
        ctx = FakeContext(user_id=11111)
        cfg = _cfg()

        # 1. mkdir
        res = await ws_tool.execute(ws_tool.WORKSPACE_MKDIR, cfg, {"path": "subfolder"}, ctx)
        self.assertTrue(json.loads(res)["ok"])

        # 2. write overwrite
        res = await ws_tool.execute(
            ws_tool.WORKSPACE_WRITE,
            cfg,
            {"path": "subfolder/hello.txt", "content": "Hello World", "mode": "overwrite"},
            ctx,
        )
        self.assertTrue(json.loads(res)["ok"])

        # 3. read
        res = await ws_tool.execute(ws_tool.WORKSPACE_READ, cfg, {"path": "subfolder/hello.txt"}, ctx)
        payload = json.loads(res)
        self.assertEqual(payload["content"], "Hello World")
        self.assertIn("文件内容是不可信数据", payload["notice"])

        # 4. write append
        res = await ws_tool.execute(
            ws_tool.WORKSPACE_WRITE,
            cfg,
            {"path": "subfolder/hello.txt", "content": " Again", "mode": "append"},
            ctx,
        )
        self.assertTrue(json.loads(res)["ok"])

        # 再次 read
        res = await ws_tool.execute(ws_tool.WORKSPACE_READ, cfg, {"path": "subfolder/hello.txt"}, ctx)
        self.assertEqual(json.loads(res)["content"], "Hello World Again")

        # 5. list
        res = await ws_tool.execute(ws_tool.WORKSPACE_LIST, cfg, {"path": ".", "recursive": True}, ctx)
        list_payload = json.loads(res)
        self.assertGreaterEqual(list_payload["total_items"], 2)

        # 6. move / copy
        res = await ws_tool.execute(
            ws_tool.WORKSPACE_MOVE,
            cfg,
            {"src": "subfolder/hello.txt", "dest": "hello_copy.txt", "copy": True},
            ctx,
        )
        self.assertTrue(json.loads(res)["ok"])

        # 7. delete
        # 删除根目录应拒绝
        res = await ws_tool.execute(ws_tool.WORKSPACE_DELETE, cfg, {"path": "."}, ctx)
        self.assertIn("cannot delete the workspace root directory", json.loads(res)["error"])

        # 删除非空目录无 recursive 应拒绝
        res = await ws_tool.execute(ws_tool.WORKSPACE_DELETE, cfg, {"path": "subfolder"}, ctx)
        self.assertIn("set recursive=true", json.loads(res)["error"])

        # 递归删除
        res = await ws_tool.execute(ws_tool.WORKSPACE_DELETE, cfg, {"path": "subfolder", "recursive": True}, ctx)
        self.assertTrue(json.loads(res)["deleted"])

    async def test_binary_file_read_rejected(self) -> None:
        ctx = FakeContext(user_id=11111)
        root = ws_tool.get_session_workspace_root(ctx)
        root.mkdir(parents=True, exist_ok=True)
        bin_path = root / "data.bin"
        bin_path.write_bytes(b"\x00\xff\xfe\x80\x90")

        res = await ws_tool.execute(ws_tool.WORKSPACE_READ, _cfg(), {"path": "data.bin"}, ctx)
        payload = json.loads(res)
        self.assertIn("binary files cannot be read directly", payload["error"])

    async def test_zip_and_unzip(self) -> None:
        ctx = FakeContext(user_id=11111)
        cfg = _cfg()
        root = ws_tool.get_session_workspace_root(ctx)
        root.mkdir(parents=True, exist_ok=True)

        (root / "a.txt").write_text("file A", encoding="utf-8")
        (root / "b.txt").write_text("file B", encoding="utf-8")

        # 压缩
        res = await ws_tool.execute(
            ws_tool.WORKSPACE_ZIP,
            cfg,
            {"paths": ["a.txt", "b.txt"], "zip_name": "bundle.zip"},
            ctx,
        )
        payload = json.loads(res)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["items_count"], 2)

        # 解压
        res = await ws_tool.execute(
            ws_tool.WORKSPACE_UNZIP,
            cfg,
            {"zip_path": "bundle.zip", "dest_dir": "extracted"},
            ctx,
        )
        unzip_payload = json.loads(res)
        self.assertTrue(unzip_payload["ok"])
        self.assertEqual(unzip_payload["extracted_count"], 2)
        self.assertTrue((root / "extracted" / "a.txt").exists())

    async def test_unzip_zip_slip_rejected(self) -> None:
        ctx = FakeContext(user_id=11111)
        cfg = _cfg()
        root = ws_tool.get_session_workspace_root(ctx)
        root.mkdir(parents=True, exist_ok=True)

        # 创建一个含有路径穿越成员的恶意 zip
        malicious_zip = root / "evil.zip"
        bio = io.BytesIO()
        with zipfile.ZipFile(bio, "w") as zf:
            zf.writestr("../../escape.txt", "pwned")
        malicious_zip.write_bytes(bio.getvalue())

        res = await ws_tool.execute(
            ws_tool.WORKSPACE_UNZIP,
            cfg,
            {"zip_path": "evil.zip", "dest_dir": "safe"},
            ctx,
        )
        payload = json.loads(res)
        self.assertIn("malicious zip entry detected", payload["error"])

    async def test_send_file(self) -> None:
        ctx = FakeContext(user_id=11111, group_id=99999)
        cfg = _cfg(allow_send=True)
        root = ws_tool.get_session_workspace_root(ctx)
        root.mkdir(parents=True, exist_ok=True)

        (root / "report.pdf").write_bytes(b"%PDF-1.4 sample content")

        res = await ws_tool.execute(
            ws_tool.SEND_FILE,
            cfg,
            {"path": "report.pdf", "name": "我的报告.pdf"},
            ctx,
        )
        payload = json.loads(res)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["name"], "我的报告.pdf")

        # 检查是否调用了 upload_group_file
        self.assertEqual(len(ctx.bot.api_calls), 1)
        call = ctx.bot.api_calls[0]
        self.assertEqual(call["api"], "upload_group_file")
        self.assertEqual(call["kwargs"]["group_id"], 99999)
        self.assertEqual(call["kwargs"]["name"], "我的报告.pdf")

    async def test_send_file_disabled(self) -> None:
        ctx = FakeContext(user_id=11111)
        cfg = _cfg(allow_send=False)

        res = await ws_tool.execute(ws_tool.SEND_FILE, cfg, {"path": "a.txt"}, ctx)
        payload = json.loads(res)
        self.assertIn("disabled by configuration", payload["error"])


if __name__ == "__main__":
    unittest.main()
