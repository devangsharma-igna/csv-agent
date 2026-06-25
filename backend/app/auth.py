from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass
from enum import StrEnum

from fastapi import Cookie, Depends, HTTPException


SESSION_COOKIE = "igna_session"


class Role(StrEnum):
    USER = "user"
    SUPER_ADMIN = "super_admin"


@dataclass(frozen=True)
class CurrentUser:
    username: str
    role: Role
    session_id: str = ""


_CREDENTIALS = {
    "igna.admin@gmail.com": ("admin@123", Role.SUPER_ADMIN),
    "igna.user@gmail.com": ("user@123", Role.USER),
}


def authenticate(username: str, password: str) -> CurrentUser:
    normalized_username = username.strip().lower()
    stored = _CREDENTIALS.get(normalized_username)
    if stored is None or not hmac.compare_digest(stored[0], password):
        raise ValueError("invalid credentials")
    return CurrentUser(username=normalized_username, role=stored[1])


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, CurrentUser] = {}

    def create(self, user: CurrentUser) -> str:
        session_id = secrets.token_urlsafe(32)
        self._sessions[session_id] = CurrentUser(
            username=user.username,
            role=user.role,
            session_id=session_id,
        )
        return session_id

    def get(self, session_id: str | None) -> CurrentUser | None:
        return self._sessions.get(session_id or "")

    def revoke(self, session_id: str | None) -> bool:
        return self._sessions.pop(session_id or "", None) is not None


sessions = SessionStore()


def require_user(
    session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> CurrentUser:
    user = sessions.get(session_id)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail={"error": "authentication_required"},
        )
    return user


def require_super_admin(
    user: CurrentUser = Depends(require_user),
) -> CurrentUser:
    if user.role != Role.SUPER_ADMIN:
        raise HTTPException(
            status_code=403,
            detail={"error": "super_admin_required"},
        )
    return user
