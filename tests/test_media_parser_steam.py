"""Steam 解析接入回归：平台标记、卡片链接提取与默认解析器装配。

背景：SteamParser 一直会被 vendored 运行时创建（`parsers` 缺少 `steam` 键时
上游按「全部发送」兜底），但本地 `SUPPORTED_LINK_MARKERS` 没有 Steam 域名，
自动解析的粗筛会把它挡在门外——只有同一条消息里恰好带了别的受支持链接时，
才会被 `extract_all_links`「顺带」解析出来。这里锁定正式接入后的行为。
"""

import copy
import json
import sys
import unittest
from pathlib import Path

# 与 plugins/astrbot_compat/__init__.py 一致：shim/ 加入 sys.path 后
# vendored core/logger.py 才会导入 shim 的 astrbot.api.logger（生产同样如此）。
_SHIM = str((Path(__file__).resolve().parent.parent / "plugins" / "astrbot_compat" / "shim").resolve())
if _SHIM not in sys.path:
    sys.path.insert(0, _SHIM)

import astrbot.api.logger  # noqa: E402

from core.defaults import DEFAULT_MEDIA_PARSER_CONFIG  # noqa: E402
from plugins.media_parser.prepare import (  # noqa: E402
    _card_url_candidates,
    is_supported_platform_url,
    text_has_supported_link,
)
from plugins.media_parser.runtime import create_runtime  # noqa: E402
from third_party.astrbot_plugin_media_parser.core.parser.platform.steam import (  # noqa: E402
    SteamParser,
)

STEAM_APP_URL = "https://store.steampowered.com/app/3971950/In_Falsus/"

# 生产环境实际触发解析的那条消息（群 165178207，2026-09-17）：Steam 链接当时
# 靠同条消息里的 x.com 链接打开粗筛闸门才被解析，自身并未命中标记表。
REAL_MESSAGE = (
    "https://youtu.be/1KdWyHHK83k\n\n"
    "▼In Falsus（Steam）\n"
    "https://store.steampowered.com/app/3971950/In_Falsus/\n\n"
    "▼公式X\n"
    "https://x.com/infalsus_jp\n"
)


class SteamPlatformMarkerTests(unittest.TestCase):
    def test_steam_message_passes_coarse_filter_on_its_own(self) -> None:
        """Steam 链接必须能单独触发自动解析，不再依赖同条消息里的其他平台链接。"""
        self.assertTrue(text_has_supported_link(STEAM_APP_URL))
        self.assertTrue(text_has_supported_link(f"这个游戏在打折 {STEAM_APP_URL}"))

    def test_steam_game_page_is_supported_platform_url(self) -> None:
        self.assertTrue(is_supported_platform_url(STEAM_APP_URL))
        self.assertTrue(
            is_supported_platform_url("https://store.steampowered.com/app/3971950")
        )

    def test_lookalike_host_is_not_supported(self) -> None:
        """后缀匹配不能把 `xxx.steampowered.com` 之外的仿冒域名放进来。"""
        self.assertFalse(
            is_supported_platform_url("https://store.steampowered.com.evil.example/app/3971950/")
        )
        self.assertFalse(is_supported_platform_url("https://example.com/app/3971950/"))
        self.assertFalse(is_supported_platform_url("https://notsteampowered.com/app/3971950/"))

    def test_steam_link_in_qq_card_is_extracted(self) -> None:
        """QQ 结构化卡片里的 Steam 链接同样要能取回（卡片只给跳转字段，不给正文）。

        `_card_url_candidates` 会同时扫描原文与解析后的嵌套 JSON，因此可能返回
        重复项，由调用方 `_extract_card_urls` / `dedupe_links` 去重。
        """
        card = {"data": json.dumps({"meta": {"detail_1": {"url": STEAM_APP_URL}}})}

        self.assertEqual({STEAM_APP_URL}, set(_card_url_candidates(card)))


class SteamParserRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = SteamParser()

    def test_can_parse_game_page_only(self) -> None:
        self.assertTrue(self.parser.can_parse(STEAM_APP_URL))
        # 商店其他页面（bundle / sub / publisher）不是游戏页，上游不解析。
        self.assertFalse(self.parser.can_parse("https://store.steampowered.com/bundle/123/"))
        self.assertFalse(self.parser.can_parse("https://store.steampowered.com/app/abc/"))
        self.assertFalse(self.parser.can_parse("https://example.com/app/3971950/"))

    def test_extract_links_keeps_one_link_per_appid(self) -> None:
        text = f"{STEAM_APP_URL} 和 {STEAM_APP_URL}?curator_clanid=1"

        links = self.parser.extract_links(text)

        self.assertEqual(1, len(links))
        self.assertTrue(links[0].startswith(STEAM_APP_URL))

    def test_extract_links_finds_steam_inside_mixed_message(self) -> None:
        self.assertEqual([STEAM_APP_URL], self.parser.extract_links(REAL_MESSAGE))


class SteamDefaultConfigTests(unittest.TestCase):
    def test_default_config_declares_steam_output_mode(self) -> None:
        """显式声明而不是靠上游“缺键=全部发送”兜底，避免默认值随上游漂移。"""
        self.assertEqual("全部发送", DEFAULT_MEDIA_PARSER_CONFIG["parsers"]["steam"])

    def test_default_config_instantiates_steam_parser(self) -> None:
        runtime = create_runtime(copy.deepcopy(DEFAULT_MEDIA_PARSER_CONFIG))

        self.assertEqual((True, True), runtime.config_manager.parser_output.output_for_controller("steam"))
        self.assertIsInstance(runtime.parser_manager.find_parser(STEAM_APP_URL), SteamParser)

    def test_default_runtime_extracts_standalone_steam_link(self) -> None:
        runtime = create_runtime(copy.deepcopy(DEFAULT_MEDIA_PARSER_CONFIG))

        links = runtime.parser_manager.extract_all_links(STEAM_APP_URL)

        self.assertEqual([STEAM_APP_URL], [url for url, _ in links])
        self.assertIsInstance(links[0][1], SteamParser)

    def test_default_runtime_extracts_steam_from_mixed_message(self) -> None:
        runtime = create_runtime(copy.deepcopy(DEFAULT_MEDIA_PARSER_CONFIG))

        urls = [url for url, _ in runtime.parser_manager.extract_all_links(REAL_MESSAGE)]

        self.assertIn(STEAM_APP_URL, urls)


if __name__ == "__main__":
    unittest.main()
