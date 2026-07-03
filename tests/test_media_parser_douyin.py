import unittest

from third_party.astrbot_plugin_media_parser.core.parser.platform.douyin import (
    DouyinParser,
)


class DouyinParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = DouyinParser()

    def test_note_images_are_not_overridden_by_malformed_playwm_url(self) -> None:
        bad_video_url = (
            "https://aweme.snssdk.com/aweme/v1/playwm/"
            "?video_id=https://sf11-cdn-tos.douyinstatic.com/obj/"
            "tos-cn-ve-2774/0dcb90f6f7d94a90857adefdfdc917ed"
            "&ratio=720p&line=0"
        )
        image_url = (
            "https://p26-sign.douyinpic.com/tos-cn-i-dy/"
            "cb16ca269d5e4ecebf0d3adcb3f9033e~tplv-dy-shrink"
            ":1440:1920.webp"
        )
        item_info = {
            "video": {
                "play_addr": {
                    "uri": (
                        "https://sf11-cdn-tos.douyinstatic.com/obj/"
                        "tos-cn-ve-2774/0dcb90f6f7d94a90857adefdfdc917ed"
                    ),
                    "url_list": [bad_video_url],
                }
            },
            "images": [
                {
                    "url_list": [image_url],
                    "height": 1920,
                    "width": 1440,
                }
            ],
        }

        video_url_lists, image_url_lists, video_cover_url_lists = (
            self.parser._extract_douyin_media_url_lists(item_info)
        )

        self.assertFalse(self.parser._looks_like_video_url(bad_video_url))
        self.assertEqual([], video_url_lists)
        self.assertEqual([[image_url]], image_url_lists)
        self.assertEqual([], video_cover_url_lists)

    def test_regular_douyin_play_uri_still_counts_as_video(self) -> None:
        item_info = {
            "video": {
                "play_addr": {
                    "uri": "v0200fg10000examplevideoid",
                    "url_list": [],
                }
            }
        }

        video_url_lists, image_url_lists, _video_cover_url_lists = (
            self.parser._extract_douyin_media_url_lists(item_info)
        )

        self.assertEqual([], image_url_lists)
        self.assertEqual(
            [["https://www.douyin.com/aweme/v1/play/?video_id=v0200fg10000examplevideoid"]],
            video_url_lists,
        )


if __name__ == "__main__":
    unittest.main()
