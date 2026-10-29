"""Session lifetime and password binding (2026-10-29).

The page session lasts 30 days, but every token carries a fingerprint of the
current password, so changing ``PARO_MYSEKAI_PASSWORD`` revokes all tokens
without rotating ``PARO_MYSEKAI_SESSION_SECRET``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import replace
from pathlib import Path

from app.config import Settings
from app.repositories.sqlite import SQLiteRepository
from app.services.mysekai import (
    SESSION_TTL_SECONDS,
    MysekaiService,
    _b64url_encode,
)


def _settings(tmp_path: Path, **overrides) -> Settings:
    base = Settings.from_env()
    values = dict(
        announcements_path=tmp_path / "announcements.json",
        database_path=tmp_path / "test.sqlite3",
        cookie_secure=False,
        allowed_hosts=("testserver",),
        allowed_origins=("http://testserver",),
        draw_cooldown_seconds=0,
        mysekai_password="secret",
        mysekai_session_secret="session-secret",
        mysekai_service_url="http://mysekai.test",
        mysekai_haruki_base="https://haruki.test",
        mysekai_asset_base="https://assets.test",
        mysekai_master_base="https://master.test",
        mysekai_internal_token="bot-token",
        mysekai_push_url="",
        mysekai_push_token="",
    )
    values.update(overrides)
    return replace(base, **values)


def _service(tmp_path: Path, **overrides) -> MysekaiService:
    settings = _settings(tmp_path, **overrides)
    return MysekaiService(settings, SQLiteRepository(settings.database_path))


def test_session_lasts_thirty_days() -> None:
    assert SESSION_TTL_SECONDS == 30 * 24 * 60 * 60


def test_token_survives_when_password_is_unchanged(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assert service.verify_token(service.issue_token()) is True


def test_token_dies_when_password_changes(tmp_path: Path) -> None:
    service = _service(tmp_path)
    token = service.issue_token()
    service.settings = _settings(tmp_path, mysekai_password="changed")
    assert service.verify_token(token) is False


def test_token_without_password_fingerprint_is_rejected(tmp_path: Path) -> None:
    service = _service(tmp_path)
    payload = json.dumps(
        {"exp": int(time.time()) + 60}, separators=(",", ":")
    ).encode("utf-8")
    body = _b64url_encode(payload)
    signature = hmac.new(
        b"session-secret", body.encode("ascii"), hashlib.sha256
    ).hexdigest()
    assert service.verify_token(f"{body}.{signature}") is False
