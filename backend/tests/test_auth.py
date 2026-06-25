import os
import unittest
from dataclasses import FrozenInstanceError

os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
os.environ.setdefault("AZURE_OPENAI_API_KEY", "test-key")

from fastapi import HTTPException

from app.auth import (
    SESSION_COOKIE,
    CurrentUser,
    Role,
    SessionStore,
    authenticate,
    require_super_admin,
    require_user,
    sessions,
)
from app.config import Settings


class AuthenticationTests(unittest.TestCase):
    def test_authenticates_super_admin(self) -> None:
        user = authenticate("IGNA.ADMIN@GMAIL.COM ", "admin@123")

        self.assertEqual(user.username, "igna.admin@gmail.com")
        self.assertEqual(user.role, Role.SUPER_ADMIN)

    def test_authenticates_read_only_user(self) -> None:
        user = authenticate("igna.user@gmail.com", "user@123")

        self.assertEqual(user.role, Role.USER)

    def test_rejects_invalid_credentials_without_revealing_account_state(self) -> None:
        for username, password in (
            ("igna.admin@gmail.com", "wrong"),
            ("unknown@example.com", "wrong"),
        ):
            with self.subTest(username=username):
                with self.assertRaisesRegex(ValueError, "^invalid credentials$"):
                    authenticate(username, password)

    def test_current_user_is_immutable(self) -> None:
        user = CurrentUser("igna.user@gmail.com", Role.USER)

        with self.assertRaises(FrozenInstanceError):
            user.role = Role.SUPER_ADMIN  # type: ignore[misc]

    def test_session_is_opaque_and_can_be_revoked(self) -> None:
        store = SessionStore()
        user = authenticate("igna.user@gmail.com", "user@123")

        session_id = store.create(user)
        stored_user = store.get(session_id)

        self.assertNotIn(user.username, session_id)
        self.assertIsNotNone(stored_user)
        self.assertEqual(stored_user.username, user.username)
        self.assertEqual(stored_user.role, user.role)
        self.assertEqual(stored_user.session_id, session_id)
        self.assertTrue(store.revoke(session_id))
        self.assertIsNone(store.get(session_id))
        self.assertFalse(store.revoke(session_id))

    def test_session_store_handles_missing_cookie(self) -> None:
        store = SessionStore()

        self.assertIsNone(store.get(None))
        self.assertFalse(store.revoke(None))


class AuthorizationDependencyTests(unittest.TestCase):
    def test_require_user_rejects_missing_or_unknown_session(self) -> None:
        for session_id in (None, "unknown"):
            with self.subTest(session_id=session_id):
                with self.assertRaises(HTTPException) as raised:
                    require_user(session_id)

                self.assertEqual(raised.exception.status_code, 401)
                self.assertEqual(
                    raised.exception.detail,
                    {"error": "authentication_required"},
                )

    def test_require_user_returns_session_user(self) -> None:
        session_id = sessions.create(
            authenticate("igna.user@gmail.com", "user@123")
        )
        self.addCleanup(sessions.revoke, session_id)

        user = require_user(session_id)

        self.assertEqual(user.username, "igna.user@gmail.com")
        self.assertEqual(user.session_id, session_id)

    def test_require_super_admin_rejects_user(self) -> None:
        user = CurrentUser("igna.user@gmail.com", Role.USER, "session-id")

        with self.assertRaises(HTTPException) as raised:
            require_super_admin(user)

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(
            raised.exception.detail,
            {"error": "super_admin_required"},
        )

    def test_require_super_admin_returns_admin(self) -> None:
        admin = CurrentUser(
            "igna.admin@gmail.com",
            Role.SUPER_ADMIN,
            "session-id",
        )

        self.assertIs(require_super_admin(admin), admin)


class AuthenticationConfigTests(unittest.TestCase):
    def test_session_cookie_and_secure_default(self) -> None:
        settings = Settings(
            AZURE_OPENAI_ENDPOINT="https://example.openai.azure.com",
            AZURE_OPENAI_API_KEY="test-key",
        )

        self.assertEqual(SESSION_COOKIE, "igna_session")
        self.assertFalse(settings.AUTH_COOKIE_SECURE)


if __name__ == "__main__":
    unittest.main()
