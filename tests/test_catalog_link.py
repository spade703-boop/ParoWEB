"""Signed catalog links: round-trip, tampering and expiry."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from app.services.catalog_link import CatalogLinkError, issue, parse

SECRET = "session-secret"


def test_round_trip_keeps_the_claims():
    token = issue(SECRET, game_user_id="123", kind="talk-list", query="冬弥 all")
    claims = parse(SECRET, token)
    assert claims["gid"] == "123"
    assert claims["kind"] == "talk-list"
    assert claims["q"] == "冬弥 all"


def test_a_tampered_body_is_rejected():
    token = issue(SECRET, game_user_id="123", kind="fixture-list")
    body, _, signature = token.partition(".")
    with pytest.raises(CatalogLinkError):
        parse(SECRET, f"{body}x.{signature}")


def test_another_secret_cannot_validate():
    token = issue(SECRET, game_user_id="123", kind="fixture-list")
    with pytest.raises(CatalogLinkError):
        parse("other-secret", token)


def test_an_expired_token_is_rejected(monkeypatch):
    token = issue(SECRET, game_user_id="123", kind="fixture-list")
    # Given the clock has moved past the 30-minute window
    monkeypatch.setattr("app.services.catalog_link.time.time", lambda: 10**12)
    with pytest.raises(CatalogLinkError):
        parse(SECRET, token)
