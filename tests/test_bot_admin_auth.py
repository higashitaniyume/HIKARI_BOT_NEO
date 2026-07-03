from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from plugins.bot_admin import handler as admin_handler


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


if __name__ == "__main__":
    unittest.main()
