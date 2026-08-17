from __future__ import annotations

import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import nonebot

nonebot.init(driver="nonebot.drivers.none:Driver")

from nonebot.adapters.onebot.v11 import GroupMessageEvent, PrivateMessageEvent

from core.command_router import CommandSpec, _scope_allowed, iter_commands
from core import command_router
from plugins.astrbot_compat import loader, manager


def _private_event(user_id: int) -> PrivateMessageEvent:
    return PrivateMessageEvent(
        time=0,
        self_id=1,
        post_type="message",
        message_type="private",
        sub_type="friend",
        font=0,
        sender={"user_id": user_id},
        user_id=user_id,
        message_id=1,
        raw_message="test",
        message=[{"type": "text", "data": {"text": "test"}}],
    )


def _group_event(user_id: int, role: str | None = "member") -> GroupMessageEvent:
    sender = {"user_id": user_id}
    if role is not None:
        sender["role"] = role
    return GroupMessageEvent(
        time=0,
        self_id=1,
        post_type="message",
        message_type="group",
        sub_type="normal",
        font=0,
        sender=sender,
        user_id=user_id,
        group_id=100,
        message_id=2,
        raw_message="test",
        message=[{"type": "text", "data": {"text": "test"}}],
    )


class CommandSecurityTests(unittest.TestCase):
    def test_superuser_only_rejects_empty_and_placeholder_config(self) -> None:
        spec = CommandSpec("protected", (), lambda ctx: None, superuser_only=True)
        event = _private_event(12345)
        for configured in ("", "你的QQ号", "0"):
            with self.subTest(configured=configured), patch(
                "core.command_router.load_main_config",
                return_value={"bot": {"superuser_id": configured}},
            ):
                self.assertFalse(_scope_allowed(spec, event))

    def test_superuser_only_accepts_matching_configured_id(self) -> None:
        spec = CommandSpec("protected", (), lambda ctx: None, superuser_only=True)
        with patch(
            "core.command_router.load_main_config",
            return_value={"bot": {"superuser_id": "12345"}},
        ):
            self.assertTrue(_scope_allowed(spec, _private_event(12345)))
            self.assertFalse(_scope_allowed(spec, _private_event(54321)))

    def test_astrbot_management_commands_are_private_superuser_only(self) -> None:
        expected = {
            "astrbot list",
            "astrbot load",
            "astrbot remove",
            "astrbot reload",
            "astrbot rebuild-env",
            "astrbot info",
        }
        specs = {spec.name: spec for spec in iter_commands() if spec.name in expected}
        self.assertEqual(set(specs), expected)
        self.assertTrue(all(spec.private_only for spec in specs.values()))
        self.assertTrue(all(spec.superuser_only for spec in specs.values()))


class AstrBotPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_superuser_permission_checks_configured_identity(self) -> None:
        bot = SimpleNamespace(get_group_member_info=AsyncMock())
        with patch(
            "core.command_router.load_main_config",
            return_value={"bot": {"superuser_id": "12345"}},
        ):
            self.assertTrue(await loader._permission_allowed("superuser", bot, _private_event(12345)))
            self.assertFalse(await loader._permission_allowed("superuser", bot, _private_event(54321)))

    async def test_admin_allows_group_owner_or_admin(self) -> None:
        bot = SimpleNamespace(get_group_member_info=AsyncMock())
        with patch(
            "core.command_router.load_main_config",
            return_value={"bot": {"superuser_id": "99999"}},
        ):
            self.assertTrue(await loader._permission_allowed("admin", bot, _group_event(1, "owner")))
            self.assertTrue(await loader._permission_allowed("admin", bot, _group_event(2, "admin")))
            self.assertFalse(await loader._permission_allowed("admin", bot, _group_event(3, "member")))

    async def test_admin_falls_back_to_group_member_api_when_role_missing(self) -> None:
        bot = SimpleNamespace(get_group_member_info=AsyncMock(return_value={"role": "admin"}))
        with patch(
            "core.command_router.load_main_config",
            return_value={"bot": {"superuser_id": "99999"}},
        ):
            self.assertTrue(await loader._permission_allowed("admin", bot, _group_event(3, None)))
        bot.get_group_member_info.assert_awaited_once()

    async def test_admin_permission_is_not_mapped_to_command_scope(self) -> None:
        async def handler(instance, event) -> None:
            return None

        handle = SimpleNamespace(
            instance=object(),
            name="test",
            command_names=[],
            command_specs=[],
            _command_aliases={},
        )
        registered = []
        with patch.object(command_router, "_commands", registered), patch.object(
            loader, "_commands", registered
        ):
            loader._register_one_command(
                handle,
                handler,
                {
                    "name": "admin-test",
                    "alias": set(),
                    "params": [],
                    "permission": "admin",
                    "event_type": "all",
                },
            )
            spec = handle.command_specs[0]
            self.assertFalse(spec.require_tome)
            self.assertFalse(spec.private_only)

            ctx = SimpleNamespace(
                event=_private_event(54321),
                text="admin-test",
                bot=SimpleNamespace(get_group_member_info=AsyncMock()),
                command="admin-test",
            )
            with (
                patch(
                    "core.command_router.load_main_config",
                    return_value={"bot": {"superuser_id": "12345"}},
                ),
                patch(
                    "plugins.astrbot_compat.dispatch._run_generator",
                    new=AsyncMock(),
                ) as run_generator,
            ):
                await spec.handler(ctx)
            run_generator.assert_not_awaited()


class PluginZipSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.plugins_dir = self.root / "plugins"
        self.plugins_dir.mkdir()
        self.plugins_patch = patch(
            "plugins.astrbot_compat.constants.PLUGINS_DIR",
            self.plugins_dir,
        )
        self.plugins_patch.start()

    def tearDown(self) -> None:
        self.plugins_patch.stop()
        self.temp_dir.cleanup()

    def _zip(self, name: str, members: dict[str, bytes | str]) -> Path:
        path = self.root / name
        with zipfile.ZipFile(path, "w") as archive:
            for member_name, content in members.items():
                archive.writestr(member_name, content)
        return path

    def test_rejects_unsafe_target_name(self) -> None:
        archive = self._zip("plugin.zip", {"main.py": "pass\n"})
        for target_name in ("../escape", "CON"):
            with self.subTest(target_name=target_name), self.assertRaises(ValueError):
                manager.extract_plugin_zip(archive, target_name)

    def test_rejects_zip_slip_and_does_not_write_outside_plugins(self) -> None:
        archive = self._zip("plugin.zip", {"main.py": "pass\n", "../escaped.txt": "bad"})
        with self.assertRaises(ValueError):
            manager.extract_plugin_zip(archive, "safe")
        self.assertFalse((self.root / "escaped.txt").exists())

    def test_rejects_windows_absolute_paths(self) -> None:
        for member in (r"C:\\temp\\bad.py", r"\\\\server\\share\\bad.py"):
            with self.subTest(member=member):
                archive = self._zip("plugin.zip", {"main.py": "pass\n", member: "bad"})
                with self.assertRaises(ValueError):
                    manager.extract_plugin_zip(archive, "safe")

    def test_rejects_symbolic_links(self) -> None:
        archive = self.root / "plugin.zip"
        link = zipfile.ZipInfo("link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("main.py", "pass\n")
            zf.writestr(link, "main.py")
        with self.assertRaises(ValueError):
            manager.extract_plugin_zip(archive, "safe")

    def test_failed_install_preserves_existing_plugin(self) -> None:
        existing = self.plugins_dir / "safe"
        existing.mkdir()
        (existing / "main.py").write_text("old = True\n", encoding="utf-8")
        archive = self._zip("plugin.zip", {"not-main.py": "new = True\n"})
        with self.assertRaises(ValueError):
            manager.extract_plugin_zip(archive, "safe")
        self.assertEqual((existing / "main.py").read_text(encoding="utf-8"), "old = True\n")

    def test_valid_single_root_zip_replaces_existing_plugin(self) -> None:
        existing = self.plugins_dir / "safe"
        existing.mkdir()
        (existing / "main.py").write_text("old = True\n", encoding="utf-8")
        archive = self._zip(
            "plugin.zip",
            {"repo/main.py": "new = True\n", "repo/data.txt": "ok"},
        )
        installed = manager.extract_plugin_zip(archive, "safe")
        self.assertEqual(installed, existing)
        self.assertEqual((installed / "main.py").read_text(encoding="utf-8"), "new = True\n")
        self.assertEqual((installed / "data.txt").read_text(encoding="utf-8"), "ok")

    def test_rejects_reported_uncompressed_size_over_limit(self) -> None:
        archive = self._zip("plugin.zip", {"main.py": "pass\n"})
        with patch.object(manager, "_MAX_ZIP_UNCOMPRESSED_BYTES", 4):
            with self.assertRaises(ValueError):
                manager.extract_plugin_zip(archive, "safe")


if __name__ == "__main__":
    unittest.main()
