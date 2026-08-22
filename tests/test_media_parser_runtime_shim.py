"""Regression: vendored media parser runtime must init with the astrbot shim active.

Upstream v7.0.0 core/logger.py binds `from astrbot.api import logger` — with our
shim that resolves to the shim *module*, not a logger instance. The vendored
config_manager then calls `logger.setLevel(...)`, which crashed in Docker with
"module 'astrbot.api.logger' has no attribute 'setLevel'" and made the media
parser runtime init fail. The shim must therefore expose instance-style methods.
"""

import asyncio
import sys
import unittest
from pathlib import Path

import aiohttp

# 与 plugins/astrbot_compat/__init__.py 一致：shim/ 加入 sys.path 后
# vendored core/logger.py 才会成功导入 shim 的 astrbot.api.logger 模块。
_SHIM = str((Path(__file__).resolve().parent.parent / "plugins" / "astrbot_compat" / "shim").resolve())
if _SHIM not in sys.path:
    sys.path.insert(0, _SHIM)

import astrbot.api.logger  # noqa: E402
from plugins.media_parser.runtime import create_media_session, create_runtime  # noqa: E402
from third_party.astrbot_plugin_media_parser.core.downloader.security import (  # noqa: E402
    session_uses_public_only_connector,
)


class ShimLoggerModuleApiTests(unittest.TestCase):
    def test_module_exposes_instance_style_api(self):
        for name in (
            "info",
            "debug",
            "warning",
            "error",
            "critical",
            "exception",
            "setLevel",
            "addFilter",
            "getChild",
        ):
            self.assertTrue(
                callable(getattr(astrbot.api.logger, name, None)),
                f"astrbot.api.logger.{name} should be callable",
            )


class MediaParserRuntimeWithShimTests(unittest.TestCase):
    def test_runtime_inits_with_shim_active(self):
        # Docker 崩溃路径：ConfigManager._parse_config 末尾调用 logger.setLevel()。
        astrbot.api.logger.setLevel(0)
        runtime = create_runtime({"parsers": {"bilibili": "全部发送"}})
        names = [type(p).__name__ for p in runtime.parser_manager.parsers]
        # 未指定的平台默认“全部发送”，10 个平台全量创建；Pixiv 已随 vendor 摘除。
        self.assertEqual(names, [
            "BilibiliParser", "DouyinParser", "TikTokParser", "KuaishouParser",
            "WeiboParser", "XiaohongshuParser", "XianyuParser", "ToutiaoParser",
            "XiaoheiheParser", "TwitterParser",
        ])
        self.assertNotIn("PixivParser", names)

    def test_shim_exception_logs_without_error(self):
        astrbot.api.logger.exception("shim exception smoke: %s", "boom")


class SecureMediaSessionTests(unittest.TestCase):
    """vendored v7.0.0 safe_request 拒绝无安全连接器的会话（Docker 实况：
    “下载会话未使用公共地址安全连接器”），媒体下载会话必须走工厂创建。"""

    def test_media_session_uses_public_only_connector(self):
        async def run():
            session = create_media_session(
                aiohttp.ClientTimeout(total=5),
                proxy_addr="http://127.0.0.1:7890",
            )
            try:
                self.assertTrue(session_uses_public_only_connector(session))
            finally:
                await session.close()

        asyncio.run(run())

    def test_plain_session_is_rejected(self):
        async def run():
            session = aiohttp.ClientSession()
            try:
                self.assertFalse(session_uses_public_only_connector(session))
            finally:
                await session.close()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
