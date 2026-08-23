from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from nonebot.adapters.onebot.v11 import Message, MessageSegment
from PIL import Image

import plugins.media_convert as plugin
from core import bot_messages
from core.command_router import iter_commands


def _default_message(key: str, **kwargs) -> str:
    current = bot_messages.DEFAULT_MESSAGES
    for part in key.split("."):
        current = current[part]
    text = str(current)
    return text.format(**kwargs) if kwargs else text


def _make_animated_gif(path: Path) -> Path:
    frames = [
        Image.new("RGB", (16, 16), "red"),
        Image.new("RGB", (16, 16), "blue"),
    ]
    frames[0].save(path, save_all=True, append_images=frames[1:], loop=0, duration=100)
    return path


def _make_static_png(path: Path) -> Path:
    Image.new("RGB", (8, 8), "white").save(path)
    return path


def _test_config(temp_root: Path, **overrides) -> dict:
    cfg = {
        "enabled": True,
        "max_video_mb": 30,
        "max_image_mb": 50,
        "download_timeout_seconds": 60,
        "ffmpeg_timeout_seconds": 300,
        "output_ttl_seconds": 600,
        "gif_fps": 15,
        "gif_width": 0,
        "gif_max_colors": 256,
        "temp_root": str(temp_root),
    }
    cfg.update(overrides)
    return cfg


class FakeEvent:
    def __init__(self, reply=None, message: Message | None = None) -> None:
        self.reply = reply
        if message is not None:
            self.message = message

    def get_user_id(self) -> str:
        return "10001"


class FakeGroupEvent(FakeEvent):
    group_id = 555


class FakeContext:
    def __init__(self, event: FakeEvent, bot=None) -> None:
        self.args = ""
        self.text = ""
        self.command = ""
        self.matched = ""
        self.event = event
        self.bot = bot or SimpleNamespace()
        self.sent: list[object] = []

    async def send(self, message) -> None:
        self.sent.append(message)


def _first_segment(message_obj) -> MessageSegment:
    return message_obj[0]


def _no_register(*args, **kwargs) -> None:
    return None


class MediaConvertRegistrationTests(unittest.TestCase):
    def test_both_commands_registered_without_scope_limits(self) -> None:
        specs = {spec.name: spec for spec in iter_commands()}

        for name in ("转mp4", "转gif"):
            self.assertIn(name, specs)
            spec = specs[name]
            self.assertFalse(spec.private_only)
            self.assertFalse(spec.group_only)
            self.assertEqual(spec.category, "媒体")
            self.assertEqual(spec.detail_key, "media_convert.help")


class IsAnimatedImageTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_animated_gif_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gif = _make_animated_gif(Path(tmp) / "anim.gif")
            self.assertTrue(await plugin._is_animated_image(gif))

    async def test_static_png_is_not_animated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            png = _make_static_png(Path(tmp) / "still.png")
            self.assertFalse(await plugin._is_animated_image(png))


class MediaConvertCommandTests(unittest.IsolatedAsyncioTestCase):
    def _reply_event(self, segments: list[MessageSegment], *, group: bool = False) -> FakeEvent:
        cls = FakeGroupEvent if group else FakeEvent
        return cls(reply=SimpleNamespace(message=Message(segments), message_id=123))

    async def test_to_mp4_with_animated_gif_sends_video_segment(self) -> None:
        async def fake_gif_to_mp4(input_path, output_path, *, timeout_seconds=300) -> None:
            output_path.write_bytes(b"\x00\x00\x00 ftypisom")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gif = _make_animated_gif(root / "anim.gif")
            ctx = FakeContext(
                self._reply_event([MessageSegment.image(file=str(gif))]),
                bot=SimpleNamespace(),
            )
            with (
                patch.object(plugin, "msg", side_effect=_default_message),
                patch.object(plugin, "get_config", return_value=_test_config(root)),
                patch.object(plugin, "_gif_to_mp4", new=fake_gif_to_mp4),
                patch.object(plugin, "register_temp_media_path", new=_no_register),
            ):
                await plugin.cmd_to_mp4(ctx)

        self.assertEqual(len(ctx.sent), 1)
        seg = _first_segment(ctx.sent[0])
        self.assertEqual(seg.type, "video")
        self.assertTrue(str(seg.data.get("file", "")).endswith(".mp4"))

    async def test_to_mp4_with_static_png_replies_not_animated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            png = _make_static_png(root / "still.png")
            ctx = FakeContext(
                self._reply_event([MessageSegment.image(file=str(png))]),
                bot=SimpleNamespace(),
            )
            with (
                patch.object(plugin, "msg", side_effect=_default_message),
                patch.object(plugin, "get_config", return_value=_test_config(root)),
            ):
                await plugin.cmd_to_mp4(ctx)

        self.assertEqual(len(ctx.sent), 1)
        self.assertIn("静态图片", str(ctx.sent[0]))

    async def test_to_mp4_with_video_reply_suggests_convert_gif(self) -> None:
        ctx = FakeContext(
            self._reply_event([MessageSegment.video(file="/nowhere/video.mp4")]),
            bot=SimpleNamespace(),
        )
        with (
            patch.object(plugin, "msg", side_effect=_default_message),
            patch.object(plugin, "get_config", return_value=_test_config(Path("."))),
        ):
            await plugin.cmd_to_mp4(ctx)

        self.assertEqual(len(ctx.sent), 1)
        self.assertIn("转gif", str(ctx.sent[0]))

    async def test_command_without_reply_replies_usage(self) -> None:
        ctx = FakeContext(FakeEvent(), bot=SimpleNamespace())
        with (
            patch.object(plugin, "msg", side_effect=_default_message),
            patch.object(plugin, "get_config", return_value=_test_config(Path("."))),
        ):
            await plugin.cmd_to_mp4(ctx)

        self.assertEqual(len(ctx.sent), 1)
        self.assertIn("用法", str(ctx.sent[0]))

    async def test_reply_without_media_replies_usage(self) -> None:
        bot = SimpleNamespace(call_api=AsyncMock(return_value={"data": {"message": []}}))
        ctx = FakeContext(
            self._reply_event([MessageSegment.text("hello")]),
            bot=bot,
        )
        with (
            patch.object(plugin, "msg", side_effect=_default_message),
            patch.object(plugin, "get_config", return_value=_test_config(Path("."))),
        ):
            await plugin.cmd_to_gif(ctx)

        self.assertEqual(len(ctx.sent), 1)
        self.assertIn("用法", str(ctx.sent[0]))

    async def test_group_event_converts_same_as_private(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            png = _make_static_png(root / "still.png")
            ctx = FakeContext(
                self._reply_event([MessageSegment.image(file=str(png))], group=True),
                bot=SimpleNamespace(),
            )
            with (
                patch.object(plugin, "msg", side_effect=_default_message),
                patch.object(plugin, "get_config", return_value=_test_config(root)),
            ):
                await plugin.cmd_to_mp4(ctx)

        self.assertEqual(len(ctx.sent), 1)
        self.assertIn("静态图片", str(ctx.sent[0]))

    async def test_get_msg_fallback_resolves_reference(self) -> None:
        bot = SimpleNamespace(call_api=AsyncMock(return_value={
            "data": {
                "message": [
                    {"type": "text", "data": {"text": "看看这个"}},
                    {"type": "image", "data": {"file": "missing.gif", "url": ""}},
                ]
            }
        }))
        event = FakeEvent(
            message=Message([MessageSegment.reply(999), MessageSegment.text("转mp4")])
        )
        ctx = FakeContext(event, bot=bot)

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(plugin, "msg", side_effect=_default_message),
                patch.object(plugin, "get_config", return_value=_test_config(Path(tmp))),
            ):
                await plugin.cmd_to_mp4(ctx)

        bot.call_api.assert_awaited_once()
        self.assertEqual(bot.call_api.await_args.kwargs.get("message_id"), 999)
        self.assertEqual(len(ctx.sent), 1)
        self.assertIn("重新发送媒体", str(ctx.sent[0]))

    async def test_to_gif_with_small_local_video_sends_image_segment(self) -> None:
        async def fake_ensure(input_path, output_path, *, options=None):
            output_path.write_bytes(b"GIF89a-fake")
            return output_path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "clip.mp4"
            video.write_bytes(b"fake-video-bytes")
            ctx = FakeContext(
                self._reply_event([MessageSegment.video(file=str(video))]),
                bot=SimpleNamespace(),
            )
            captured: dict = {}

            async def capture_ensure(input_path, output_path, *, options=None):
                captured["options"] = options
                return await fake_ensure(input_path, output_path)

            with (
                patch.object(plugin, "msg", side_effect=_default_message),
                patch.object(plugin, "get_config", return_value=_test_config(root)),
                patch.object(plugin, "ensure_sticker_gif", new=capture_ensure),
                patch.object(plugin, "register_temp_media_path", new=_no_register),
            ):
                await plugin.cmd_to_gif(ctx)

        self.assertEqual(len(ctx.sent), 1)
        seg = _first_segment(ctx.sent[0])
        self.assertEqual(seg.type, "image")
        self.assertTrue(str(seg.data.get("file", "")).endswith(".gif"))
        self.assertIsNotNone(captured.get("options"))

    async def test_to_gif_with_animated_image_replies_already_gif(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gif = _make_animated_gif(root / "anim.gif")
            ctx = FakeContext(
                self._reply_event([MessageSegment.image(file=str(gif))]),
                bot=SimpleNamespace(),
            )
            with (
                patch.object(plugin, "msg", side_effect=_default_message),
                patch.object(plugin, "get_config", return_value=_test_config(root)),
            ):
                await plugin.cmd_to_gif(ctx)

        self.assertEqual(len(ctx.sent), 1)
        self.assertIn("已经是动图", str(ctx.sent[0]))

    async def test_to_gif_with_oversized_video_replies_video_too_large(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "big.mp4"
            with video.open("wb") as f:
                f.seek(2 * 1024 * 1024 - 1)
                f.write(b"\0")
            ctx = FakeContext(
                self._reply_event([MessageSegment.video(file=str(video))]),
                bot=SimpleNamespace(),
            )
            with (
                patch.object(plugin, "msg", side_effect=_default_message),
                patch.object(
                    plugin,
                    "get_config",
                    return_value=_test_config(root, max_video_mb=1),
                ),
            ):
                await plugin.cmd_to_gif(ctx)

        self.assertEqual(len(ctx.sent), 1)
        self.assertIn("超过 1MB", str(ctx.sent[0]))


if __name__ == "__main__":
    unittest.main()
