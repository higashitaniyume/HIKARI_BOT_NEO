from __future__ import annotations

import unittest

from PIL import Image, ImageDraw

from core.rendering import draw_text, load_font, text_bbox, text_size


class RenderingTextMetricTests(unittest.TestCase):
    def test_text_size_uses_top_left_anchor(self) -> None:
        font = load_font(34)
        draw = ImageDraw.Draw(Image.new("RGB", (200, 80), "white"))

        bbox = text_bbox(draw, "Ag国", font)

        self.assertEqual(bbox[1], 0)
        self.assertEqual(text_size(draw, "Ag国", font), (bbox[2] - bbox[0], bbox[3] - bbox[1]))

    def test_draw_text_accepts_configured_font(self) -> None:
        font = load_font(24)
        image = Image.new("L", (160, 60), 0)
        draw = ImageDraw.Draw(image)

        draw_text(draw, (8, 10), "测试 text", font=font, fill=255)

        self.assertIsNotNone(image.getbbox())
