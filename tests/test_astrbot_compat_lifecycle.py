from __future__ import annotations

import asyncio
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import nonebot

nonebot.init(driver="nonebot.drivers.none:Driver")

from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import get_registered_star_classes
from core.command_router import CommandSpec, _commands
from plugins.astrbot_compat import dispatch, loader, runtime, state


class _Event:
    def get_session_id(self) -> str:
        return "test-session"


class AstrBotLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._commands_before = list(_commands)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.config_patch = patch(
            "plugins.astrbot_compat.config.build_config_path",
            side_effect=lambda name: self.root / "config" / f"{name}.json",
        )
        self.config_patch.start()

    async def asyncTearDown(self) -> None:
        for name in list(state.loaded_plugins):
            await loader.unload_plugin(name)
        _commands[:] = self._commands_before
        state.regex_matchers.clear()
        state.on_message_handlers.clear()
        for module_name in list(sys.modules):
            if module_name.startswith("_astrbot_plugin_"):
                sys.modules.pop(module_name, None)
        from astrbot.api.star import clear_star_registration

        for module_name in get_registered_star_classes():
            if module_name.startswith("_astrbot_plugin_"):
                clear_star_registration(module_name)
        runtime.clear_running_loop()
        self.config_patch.stop()
        self.temp_dir.cleanup()

    def _plugin(self, directory: str, main: str, helper: str | None = None) -> Path:
        plugin_dir = self.root / directory
        plugin_dir.mkdir()
        (plugin_dir / "main.py").write_text(main, encoding="utf-8")
        if helper is not None:
            (plugin_dir / "helper.py").write_text(helper, encoding="utf-8")
        return plugin_dir

    async def test_unique_main_packages_support_relative_imports(self) -> None:
        source = (
            "from .helper import VALUE\n"
            "from astrbot.api.star import Star\n"
            "class Plugin(Star):\n"
            "    async def initialize(self):\n"
            "        self.value = VALUE\n"
        )
        first = await loader.load_plugin(
            self._plugin("first", source, "VALUE = 'first'\n"),
            plugin_name="first",
        )
        second = await loader.load_plugin(
            self._plugin("second", source, "VALUE = 'second'\n"),
            plugin_name="second",
        )

        self.assertNotEqual(first.module_prefix, second.module_prefix)
        self.assertEqual(first.instance.value, "first")
        self.assertEqual(second.instance.value, "second")
        self.assertIn(f"{first.module_prefix}.helper", sys.modules)
        self.assertIn(f"{second.module_prefix}.helper", sys.modules)
        self.assertNotIn(str(first.module_path.resolve()), sys.path)
        self.assertNotIn(str(second.module_path.resolve()), sys.path)

    async def test_initialize_and_terminate_run_on_current_loop(self) -> None:
        source = (
            "import asyncio\n"
            "from astrbot.api.star import Star\n"
            "class Plugin(Star):\n"
            "    async def initialize(self):\n"
            "        self.initialize_loop = asyncio.get_running_loop()\n"
            "    async def terminate(self):\n"
            "        self.terminate_loop = asyncio.get_running_loop()\n"
        )
        handle = await loader.load_plugin(self._plugin("loops", source), "loops")
        state.loaded_plugins["loops"] = handle
        current_loop = asyncio.get_running_loop()
        self.assertIs(handle.instance.initialize_loop, current_loop)

        await loader.unload_plugin("loops")
        self.assertIs(handle.instance.terminate_loop, current_loop)

    async def test_initialize_failure_rolls_back_everything(self) -> None:
        marker = self.root / "terminated.txt"
        source = (
            "from astrbot.api.star import Star\n"
            "from astrbot.api.event import filter\n"
            "class Plugin(Star):\n"
            "    @filter.command('failed-command')\n"
            "    async def command(self, event):\n"
            "        return None\n"
            "    async def initialize(self):\n"
            "        raise RuntimeError('boom')\n"
            "    async def terminate(self):\n"
            f"        open({str(marker)!r}, 'w', encoding='utf-8').write('yes')\n"
        )
        plugin_dir = self._plugin("broken", source)
        prefix = loader._module_prefix("broken", plugin_dir)

        with self.assertRaisesRegex(ValueError, "initialize.*failed"):
            await loader.load_plugin(plugin_dir, "broken")

        self.assertTrue(marker.exists())
        self.assertFalse(any(spec.name == "failed-command" for spec in _commands))
        self.assertFalse(any(name == prefix or name.startswith(f"{prefix}.") for name in sys.modules))
        self.assertFalse(any(name == prefix or name.startswith(f"{prefix}.") for name in get_registered_star_classes()))
        self.assertFalse(any(item.plugin_name == "broken" for item in state.regex_matchers))
        self.assertFalse(any(item.plugin_name == "broken" for item in state.on_message_handlers))

    async def test_unload_preserves_native_command_with_same_name(self) -> None:
        async def native_handler(ctx) -> None:
            return None

        native = CommandSpec("collision", (), native_handler)
        _commands.append(native)
        source = (
            "from astrbot.api.star import Star\n"
            "from astrbot.api.event import filter\n"
            "class Plugin(Star):\n"
            "    @filter.command('collision')\n"
            "    async def command(self, event):\n"
            "        return None\n"
        )
        handle = await loader.load_plugin(self._plugin("collision", source), "collision")
        state.loaded_plugins["collision"] = handle
        self.assertEqual(sum(spec.name == "collision" for spec in _commands), 2)

        await loader.unload_plugin("collision")

        self.assertIn(native, _commands)
        self.assertEqual([spec for spec in _commands if spec.name == "collision"], [native])


class AstrBotDispatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        state.loaded_plugins.clear()
        state.regex_matchers.clear()
        state.on_message_handlers.clear()
        self.bot = SimpleNamespace(send=AsyncMock())
        self.event = _Event()

    def tearDown(self) -> None:
        state.loaded_plugins.clear()
        state.regex_matchers.clear()
        state.on_message_handlers.clear()

    def _astr_event(self) -> AstrMessageEvent:
        return AstrMessageEvent("test", session_id="test-session", bot=self.bot, event=self.event)

    async def _run(self, handler) -> dispatch.DispatchResult:
        return await dispatch._run_generator(
            object(),
            handler,
            self._astr_event(),
            self.bot,
            self.event,
        )

    async def test_silent_handler_does_not_consume(self) -> None:
        async def handler(instance, event):
            return None

        result = await self._run(handler)
        self.assertFalse(result.consumed)
        self.bot.send.assert_not_awaited()

    async def test_nonempty_string_send_consumes(self) -> None:
        async def handler(instance, event):
            return "hello"

        result = await self._run(handler)
        self.assertTrue(result.consumed)
        self.assertTrue(result.sent)
        self.bot.send.assert_awaited_once()

    async def test_event_send_then_exception_still_consumes(self) -> None:
        async def handler(instance, event):
            await event.send("hello")
            raise RuntimeError("after send")

        result = await self._run(handler)
        self.assertTrue(result.consumed)
        self.assertIsInstance(result.exception, RuntimeError)
        self.bot.send.assert_awaited_once()

    async def test_exception_before_side_effect_does_not_consume(self) -> None:
        async def handler(instance, event):
            raise RuntimeError("before send")

        result = await self._run(handler)
        self.assertFalse(result.consumed)
        self.assertIsInstance(result.exception, RuntimeError)

    async def test_set_result_is_drained_once_and_consumes(self) -> None:
        async def handler(instance, event):
            result = event.plain_result("set result")
            event.set_result(result)
            return result

        result = await self._run(handler)
        self.assertTrue(result.consumed)
        self.assertTrue(result.result_set)
        self.bot.send.assert_awaited_once()

    async def test_stop_consumes_and_stops_later_on_message_handlers(self) -> None:
        calls: list[str] = []

        async def first(instance, event):
            calls.append("first")
            event.stop_event()

        async def second(instance, event):
            calls.append("second")
            return "should not send"

        state.loaded_plugins.update({
            "first": SimpleNamespace(instance=object()),
            "second": SimpleNamespace(instance=object()),
        })
        state.on_message_handlers.extend([
            state.OnMsgHandler("first", first),
            state.OnMsgHandler("second", second),
        ])
        with patch("plugins.astrbot_compat.loader._make_astr_event", side_effect=lambda bot, event, text: self._astr_event()):
            handled = await dispatch.dispatch_on_message(self.bot, self.event, "test")

        self.assertTrue(handled)
        self.assertEqual(calls, ["first"])

    async def test_regex_match_consumes_even_when_handler_is_silent(self) -> None:
        async def silent(instance, event):
            return None

        state.loaded_plugins["regex"] = SimpleNamespace(instance=object())
        state.regex_matchers.append(state.RegexMatcher("regex", __import__("re").compile("test"), silent))
        with patch("plugins.astrbot_compat.loader._make_astr_event", side_effect=lambda bot, event, text: self._astr_event()):
            matched = await dispatch.dispatch_regex_command(self.bot, self.event, "test")
        self.assertTrue(matched)


class RuntimeBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        runtime.clear_running_loop()

    async def test_worker_thread_submits_to_running_loop(self) -> None:
        loop = asyncio.get_running_loop()
        runtime.set_running_loop(loop)

        async def identify() -> tuple[asyncio.AbstractEventLoop, int]:
            return asyncio.get_running_loop(), threading.get_ident()

        result_loop, result_thread = await asyncio.to_thread(runtime.submit_coroutine, identify())
        self.assertIs(result_loop, loop)
        self.assertEqual(result_thread, threading.get_ident())

    async def test_unavailable_loop_closes_coroutine_and_raises(self) -> None:
        async def unused() -> None:
            return None

        coro = unused()
        with self.assertRaisesRegex(RuntimeError, "not available"):
            runtime.submit_coroutine(coro)
        self.assertIsNone(coro.cr_frame)

    async def test_loop_thread_sync_wait_is_rejected(self) -> None:
        runtime.set_running_loop(asyncio.get_running_loop())

        async def unused() -> None:
            return None

        coro = unused()
        with self.assertRaisesRegex(RuntimeError, "event loop thread"):
            runtime.submit_coroutine(coro)
        self.assertIsNone(coro.cr_frame)


if __name__ == "__main__":
    unittest.main()
