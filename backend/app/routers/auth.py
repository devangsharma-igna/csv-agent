from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from ..auth import (
    SESSION_COOKIE,
    CurrentUser,
    authenticate,
    require_user,
    sessions,
)
from ..config import settings


router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


def _identity(user: CurrentUser) -> dict[str, str]:
    return {"username": user.username, "role": user.role.value}


@router.post("/login")
async def login(credentials: LoginRequest, response: Response) -> dict[str, str]:
    try:
        user = authenticate(credentials.username, credentials.password)
    except ValueError:
        raise HTTPException(
            status_code=401,
            detail={"error": "invalid_credentials"},
        ) from None

    session_id = sessions.create(user)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_id,
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite=settings.AUTH_COOKIE_SAMESITE,
        path="/",
    )
    return _identity(user)


@router.get("/me")
async def me(user: CurrentUser = Depends(require_user)) -> dict[str, str]:
    return _identity(user)


@router.post("/logout")
async def logout(
    response: Response,
    user: CurrentUser = Depends(require_user),
) -> dict[str, str]:
    sessions.revoke(user.session_id)
    response.delete_cookie(
        key=SESSION_COOKIE,
        httponly=True,
        secure=settings.AUTH_COOKIE_SECURE,
        samesite=settings.AUTH_COOKIE_SAMESITE,
        path="/",
    )
    return {"status": "ok"}
