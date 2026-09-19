from __future__ import annotations

import hashlib
import secrets

from fastapi import Request, Response

from app.repositories.sqlite import SQLiteRepository


COOKIE_NAME = "paro_visitor"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class SessionManager:
    def __init__(self, repository: SQLiteRepository, *, secure: bool) -> None:
        self.repository = repository
        self.secure = secure

    async def resolve(self, request: Request) -> tuple[dict, str, bool]:
        token = request.cookies.get(COOKIE_NAME)
        is_new = not token or len(token) < 32
        if is_new:
            token = secrets.token_urlsafe(32)
        visitor = await self.repository.get_or_create_visitor(token_hash(token))
        return visitor, token, is_new

    def set_cookie(self, response: Response, token: str) -> None:
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            secure=self.secure,
            samesite="lax",
            path="/",
        )

    def delete_cookie(self, response: Response) -> None:
        response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=self.secure, samesite="lax")

