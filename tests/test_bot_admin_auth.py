from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from plugins.bot_admin import handler as admin_handler
from plugins.bot_admin import handlers_state as admin_state_handlers
from plugins.bot_admin import server as admin_server
from plugins.bot_admin.config import is_obviously_weak_password


class _Headers(dict):
    def get(self, key: str, default: str = "") -> str:
        return str(super().get(key, default))


class BotAdminAuthTests(unittest.TestCase):
    def _handler(self, path: str, headers: dict[str, str] | None = None) -> admin_handler.BotAdminHandler:
        handler = object.__new__(admin_handler.BotAdminHandler)
        handler.path = path
        handler.headers = _Headers(headers or {})
        return handler

    def test_api_accepts_authorization_bearer_password(self) -> None:
        request = self._handler("/api/aiagent-config", {"Authorization": "Bearer secret"})
        with (
            patch.object(admin_handler, "_auth_enabled", Mock(return_value=True)),
            patch.object(admin_handler, "_auth_password", Mock(return_value="secret")),
        ):
            self.assertTrue(request._is_authenticated())

    def test_api_accepts_admin_token_header(self) -> None:
        request = self._handler("/api/state", {"X-Admin-Token": "secret"})
        with (
            patch.object(admin_handler, "_auth_enabled", Mock(return_value=True)),
            patch.object(admin_handler, "_auth_password", Mock(return_value="secret")),
        ):
            self.assertTrue(request._is_authenticated())

    def test_api_rejects_wrong_token_without_cookie(self) -> None:
        request = self._handler("/api/state", {"X-Admin-Token": "wrong"})
        with (
            patch.object(admin_handler, "_auth_enabled", Mock(return_value=True)),
            patch.object(admin_handler, "_auth_password", Mock(return_value="secret")),
        ):
            self.assertFalse(request._is_authenticated())

    def test_header_token_does_not_authenticate_non_api_pages(self) -> None:
        request = self._handler("/", {"X-Admin-Token": "secret"})
        with (
            patch.object(admin_handler, "_auth_enabled", Mock(return_value=True)),
            patch.object(admin_handler, "_auth_password", Mock(return_value="secret")),
        ):
            self.assertFalse(request._is_authenticated())

    def test_cookie_session_still_authenticates_pages(self) -> None:
        request = self._handler("/", {"Cookie": f"{admin_handler._COOKIE_NAME}=valid"})
        with (
            patch.object(admin_handler, "_auth_enabled", Mock(return_value=True)),
            patch.object(admin_handler, "_valid_session_token", Mock(return_value=True)),
        ):
            self.assertTrue(request._is_authenticated())

    def test_version_api_returns_runtime_info_state(self) -> None:
        request = self._handler("/api/version")
        payload = {"current": {"version": "0.0.1", "git_hash": "abcdef1", "title": "Initial"}}
        with (
            patch.object(admin_handler.BotAdminHandler, "_is_authenticated", Mock(return_value=True)),
            patch.object(admin_state_handlers, "runtime_info_state", Mock(return_value=payload)),
            patch.object(admin_handler.BotAdminHandler, "_send_json") as send_json,
        ):
            request.do_GET()

        send_json.assert_called_once_with(payload)


class BotAdminPasswordWarningTests(unittest.TestCase):
    def test_obviously_weak_password_detection(self) -> None:
        for password in (None, "", "  ", "change-me", "PASSWORD", "admin", 123456, "qwerty"):
            with self.subTest(password=password):
                self.assertTrue(is_obviously_weak_password(password))

        self.assertFalse(is_obviously_weak_password("correct-horse-battery-staple"))

    def test_start_server_warns_without_rejecting_weak_password(self) -> None:
        fake_server = Mock()
        fake_thread = Mock()
        config = {
            "enabled": True,
            "host": "192.168.31.2",
            "port": 54213,
            "password": "change-me",
        }

        with (
            patch.object(admin_server, "_server_started", False),
            patch.object(admin_server, "get_config", return_value=config),
            patch.object(admin_server, "ThreadingHTTPServer", return_value=fake_server) as http_server,
            patch.object(admin_server.threading, "Thread", return_value=fake_thread),
            patch.object(admin_server.logger, "critical") as critical,
        ):
            admin_server.start_server()

        critical.assert_called_once()
        self.assertNotIn(config["password"], critical.call_args.args[0])
        http_server.assert_called_once_with(("192.168.31.2", 54213), admin_server.BotAdminHandler)
        fake_thread.start.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
