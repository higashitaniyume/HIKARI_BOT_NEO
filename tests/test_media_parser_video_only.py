"""「仅视频」输出模式回归：模式归一化、图片过滤与游戏信息文本。

`parsers.<平台> = 仅视频` 是本地扩展：只发送该链接解析出的视频，图片不发；游戏信息等
文本随合并转发首条一起发出，不单独发文本消息。上游把不认识的模式当成「关闭」
（`_parser_enabled` → `controller_has_any_output`），所以 `normalize_output_modes()`
先把它转成上游的「全部发送」，再由本地发送链丢掉图片——这里锁定这几步的衔接。
"""

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

from core.bot_messages import DEFAULT_MESSAGES  # noqa: E402
from core.defaults import DEFAULT_MEDIA_PARSER_CONFIG  # noqa: E402
from plugins.media_parser.config import (  # noqa: E402
    OUTPUT_MODE_VIDEO_ONLY,
    UPSTREAM_MODE_FULL,
    VIDEO_ONLY_PLATFORMS_KEY,
    normalize_output_modes,
    video_only_platforms,
)
from plugins.media_parser.prepare import (  # noqa: E402
    _apply_output_modes,
    _limit_metadata_for_send,
)
from plugins.media_parser.runtime import create_runtime  # noqa: E402
from plugins.media_parser.sender import build_media_messages, build_metadata_text  # noqa: E402

STEAM_APP_URL = "https://store.steampowered.com/app/3971950/In_Falsus/"
EXAMPLE_CONFIG = (
    Path(__file__).resolve().parent.parent / "BotData" / "plugin_configs" / "media_parser.example.json"
)
EXAMPLE_MESSAGES = (
    Path(__file__).resolve().parent.parent / "BotData" / "resources" / "bot_messages.example.json"
)


def _steam_metadata(**overrides) -> dict:
    metadata = {
        "platform": "steam",
        "parser_name": "steam",
        "source_url": STEAM_APP_URL,
        "title": "In Falsus",
        "video_urls": [["https://cdn.example/video_0.mp4"], ["https://cdn.example/video_1.mp4"]],
        "image_urls": [[f"https://cdn.example/image_{index}.jpg"] for index in range(20)],
    }
    metadata.update(overrides)
    return metadata


class VideoOnlyModeNormalizationTests(unittest.TestCase):
    def test_video_only_is_translated_for_upstream(self) -> None:
        """「仅视频」必须变成上游认得的「全部发送」，否则 Steam 会被当成关闭。"""
        cfg = normalize_output_modes({"parsers": {"steam": "仅视频"}})

        self.assertEqual(UPSTREAM_MODE_FULL, cfg["parsers"]["steam"])
        self.assertEqual(["steam"], cfg[VIDEO_ONLY_PLATFORMS_KEY])
        self.assertEqual({"steam"}, video_only_platforms(cfg))

    def test_other_modes_are_untouched(self) -> None:
        cfg = normalize_output_modes(
            {"parsers": {"bilibili": "全部发送", "douyin": "关闭", "twitter": "仅文本"}}
        )

        self.assertEqual(
            {"bilibili": "全部发送", "douyin": "关闭", "twitter": "仅文本"},
            cfg["parsers"],
        )
        self.assertNotIn(VIDEO_ONLY_PLATFORMS_KEY, cfg)
        self.assertEqual(set(), video_only_platforms(cfg))

    def test_normalization_is_idempotent(self) -> None:
        """重复归一化（get_config + create_runtime 都会调用）不能丢掉仅视频记录。"""
        cfg = normalize_output_modes({"parsers": {"steam": "仅视频"}})

        normalize_output_modes(cfg)

        self.assertEqual(UPSTREAM_MODE_FULL, cfg["parsers"]["steam"])
        self.assertEqual(["steam"], cfg[VIDEO_ONLY_PLATFORMS_KEY])

    def test_normalized_config_still_creates_steam_parser(self) -> None:
        """归一化后上游仍然会实例化 SteamParser（没被误判成关闭）。"""
        runtime = create_runtime({"parsers": {"steam": "仅视频"}})
        names = [type(parser).__name__ for parser in runtime.parser_manager.parsers]

        self.assertIn("SteamParser", names)
        self.assertEqual({"steam"}, video_only_platforms(runtime.config))


class VideoOnlyDefaultConfigTests(unittest.TestCase):
    def test_steam_defaults_to_video_only(self) -> None:
        self.assertEqual(OUTPUT_MODE_VIDEO_ONLY, DEFAULT_MEDIA_PARSER_CONFIG["parsers"]["steam"])

    def test_example_config_matches_default(self) -> None:
        example = json.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))

        self.assertEqual(
            DEFAULT_MEDIA_PARSER_CONFIG["parsers"]["steam"],
            example["parsers"]["steam"],
        )


class VideoOnlyOutputModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = create_runtime({"parsers": {"steam": "仅视频"}})

    def test_video_only_metadata_marks_flags(self) -> None:
        metadata = _steam_metadata()

        self.assertTrue(_apply_output_modes(self.runtime, metadata))
        # 只发视频：图片不发；游戏信息文本仍保留，但要随合并转发首条一起发出
        self.assertTrue(metadata["_enable_text_metadata"])
        self.assertTrue(metadata["_enable_rich_media"])
        self.assertTrue(metadata["_video_only"])

    def test_video_only_platform_without_video_keeps_item_for_notice(self) -> None:
        """没有视频时保留条目（标记仅视频）但不带文本，由发送链回「没有可发送的视频」。"""
        metadata = _steam_metadata(video_urls=[])

        self.assertTrue(_apply_output_modes(self.runtime, metadata))
        self.assertTrue(metadata["_video_only"])
        self.assertFalse(metadata["_enable_text_metadata"])

    def test_other_platform_keeps_text_and_images(self) -> None:
        runtime = create_runtime({"parsers": {"bilibili": "全部发送"}})
        metadata = {
            "platform": "bilibili",
            "source_url": "https://www.bilibili.com/video/BV1xx411c7mD",
            "title": "标题",
            "video_urls": [["https://cdn.example/video.mp4"]],
            "image_urls": [["https://cdn.example/image.jpg"]],
        }

        self.assertTrue(_apply_output_modes(runtime, metadata))
        self.assertTrue(metadata["_enable_text_metadata"])
        self.assertFalse(metadata["_video_only"])

    def test_video_only_drops_images_before_download(self) -> None:
        metadata = _steam_metadata()
        metadata["_video_only"] = True

        limited = _limit_metadata_for_send(metadata, max_send=8)

        self.assertEqual(2, len(limited["video_urls"]))
        self.assertEqual([], limited["image_urls"])
        self.assertEqual(20, limited["_original_image_count"])

    def test_video_only_keeps_video_under_max_send(self) -> None:
        metadata = _steam_metadata()
        metadata["_video_only"] = True

        limited = _limit_metadata_for_send(metadata, max_send=1)

        self.assertEqual(1, len(limited["video_urls"]))
        self.assertEqual([], limited["image_urls"])

    def test_sender_skips_images_for_video_only(self) -> None:
        metadata = _steam_metadata(
            _video_only=True,
            video_modes=["direct", "direct"],
            image_modes=["direct"] * 20,
        )

        messages = build_media_messages(metadata, max_send=8)

        self.assertEqual(["video", "video"], [kind for kind, _ in messages])

    def test_sender_keeps_images_without_video_only(self) -> None:
        metadata = _steam_metadata(
            video_modes=["direct", "direct"],
            image_modes=["direct"] * 20,
        )

        messages = build_media_messages(metadata, max_send=4)

        self.assertEqual(["video", "video", "image", "image"], [kind for kind, _ in messages])


class VideoOnlyMessageTests(unittest.TestCase):
    def test_game_info_omits_image_count(self) -> None:
        """仅视频平台的游戏信息不写「图片 N」，否则会和实际发送内容矛盾。"""
        text = build_metadata_text(
            {
                "platform": "steam",
                "title": "In Falsus",
                "_video_only": True,
                "_original_video_count": 7,
                "_original_image_count": 20,
            },
            max_desc_chars=600,
        )

        self.assertIn("媒体：视频 7", text)
        self.assertNotIn("图片", text)

    def test_no_video_message_is_defined(self) -> None:
        """仅视频平台没解析到视频时用独立文案，避免和「没有图片/视频」混淆。"""
        self.assertIn("no_video", DEFAULT_MESSAGES["media_parser"])

    def test_example_messages_match_default(self) -> None:
        example = json.loads(EXAMPLE_MESSAGES.read_text(encoding="utf-8"))

        for key in ("no_video", "info_media_count_video"):
            self.assertEqual(
                DEFAULT_MESSAGES["media_parser"][key],
                example["media_parser"][key],
            )


if __name__ == "__main__":
    unittest.main()