"""Signed links that open one catalog card in an ordinary browser.

The bot only replies with a URL for the two full views (the whole craftable
catalog and a character's complete talk list); the browser then asks the public
route for the image. The token carries who and what, so the route needs no
session, and it expires so a leaked link stops working.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Final

__all__ = ["LINK_TTL_SECONDS", "CatalogLinkError", "issue", "parse"]

LINK_TTL_SECONDS: Final[int] = 30 * 60


class CatalogLinkError(Exception):
    """The token is missing, malformed, expired or signed with another secret."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _signature(secret: str, body: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
    ).digest()
    return _b64(digest)


def issue(secret: str, *, game_user_id: str, kind: str, query: str = "") -> str:
    """A ``body.signature`` token describing one card and its expiry."""
    payload = {
        "gid": game_user_id,
        "kind": kind,
        "q": query,
        "exp": int(time.time()) + LINK_TTL_SECONDS,
    }
    body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{body}.{_signature(secret, body)}"


def parse(secret: str, token: str) -> dict:
    """The claims inside ``token``; raises when it is forged or expired."""
    body, _, signature = token.partition(".")
    if not body or not signature:
        raise CatalogLinkError("链接格式不正确")
    if not hmac.compare_digest(_signature(secret, body), signature):
        raise CatalogLinkError("链接签名不正确")
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        raise CatalogLinkError("链接内容无法解析") from None
    if not isinstance(payload, dict):
        raise CatalogLinkError("链接内容无法解析")
    if int(payload.get("exp") or 0) < time.time():
        raise CatalogLinkError("链接已过期，请重新查询")
    return payload
