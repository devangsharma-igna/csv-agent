import os
import unittest
from dataclasses import FrozenInstanceError

os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
os.environ.setdefault("AZURE_OPENAI_API_KEY", "test-key")

from fastapi import HTTPException
from fastapi.testclient import TestClient

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
from app.config import settings
from app.main import app


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


class AuthenticationEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_login_sets_session_cookie_contract(self) -> None:
        original_secure = settings.AUTH_COOKIE_SECURE
        settings.AUTH_COOKIE_SECURE = True
        self.addCleanup(
            setattr,
            settings,
            "AUTH_COOKIE_SECURE",
            original_secure,
        )

        response = self.client.post(
            "/api/auth/login",
            json={
                "username": "igna.user@gmail.com",
                "password": "user@123",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"username": "igna.user@gmail.com", "role": "user"},
        )
        cookie = response.headers["set-cookie"]
        self.assertIn(f"{SESSION_COOKIE}=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=strict", cookie)
        self.assertIn("Path=/", cookie)
        self.assertIn("Secure", cookie)
        self.assertNotIn("Max-Age", cookie)
        self.assertNotIn("expires=", cookie.lower())

    def test_login_rejects_bad_password_generically(self) -> None:
        response = self.client.post(
            "/api/auth/login",
            json={
                "username": "igna.user@gmail.com",
                "password": "wrong",
            },
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            {"detail": {"error": "invalid_credentials"}},
        )
        self.assertNotIn("set-cookie", response.headers)

    def test_me_returns_authenticated_identity(self) -> None:
        login = self.client.post(
            "/api/auth/login",
            json={
                "username": "igna.admin@gmail.com",
                "password": "admin@123",
            },
        )

        response = self.client.get("/api/auth/me")

        self.assertEqual(login.status_code, 200)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "username": "igna.admin@gmail.com",
                "role": "super_admin",
            },
        )

    def test_logout_revokes_session_and_clears_cookie(self) -> None:
        login = self.client.post(
            "/api/auth/login",
            json={
                "username": "igna.user@gmail.com",
                "password": "user@123",
            },
        )
        session_id = login.cookies[SESSION_COOKIE]

        response = self.client.post("/api/auth/logout")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertIsNone(sessions.get(session_id))
        cookie = response.headers["set-cookie"]
        self.assertIn(f"{SESSION_COOKIE}=", cookie)
        self.assertIn("Max-Age=0", cookie)
        self.assertIn("Path=/", cookie)

        me = self.client.get("/api/auth/me")
        self.assertEqual(me.status_code, 401)
        self.assertEqual(
            me.json(),
            {"detail": {"error": "authentication_required"}},
        )

    def test_cors_allows_browser_credentials(self) -> None:
        response = self.client.options(
            "/api/auth/me",
            headers={
                "Origin": settings.FRONTEND_ORIGIN,
                "Access-Control-Request-Method": "GET",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["access-control-allow-credentials"],
            "true",
        )


if __name__ == "__main__":
    unittest.main()
