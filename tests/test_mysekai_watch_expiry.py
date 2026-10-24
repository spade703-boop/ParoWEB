from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from app.repositories.sqlite import SQLiteRepository
from app.services.mysekai import MysekaiError, MysekaiService

CN = timezone(timedelta(hours=8))
QQ = "123456789"
QQ2 = "223456789"
BOT_TOKEN = "bot-token"


def _settings(tmp_path) -> Settings:
    return replace(
        Settings.from_env(),
        database_path=tmp_path / "test.sqlite3",
        mysekai_password="secret",
        mysekai_session_secret="session-secret",
        mysekai_internal_token=BOT_TOKEN,
    )


async def _service(tmp_path) -> tuple[MysekaiService, SQLiteRepository]:
    settings = _settings(tmp_path)
    repository = SQLiteRepository(settings.database_path)
    await repository.initialize()
    return MysekaiService(settings, repository), repository


def _state(
    *,
    active: bool = False,
    start: datetime | None = None,
    end: datetime | None = None,
    next_start: datetime | None = None,
    name: str | None = None,
    next_name: str | None = None,
) -> dict:
    return {
        "active": active,
        "character_id": 3 if active else None,
        "character_name": name,
        "start_ms": int(start.timestamp() * 1000) if start else None,
        "end_ms": int(end.timestamp() * 1000) if end else None,
        "next_character_id": None,
        "next_character_name": next_name,
        "next_start_ms": int(next_start.timestamp() * 1000) if next_start else None,
    }


async def _store_window(repository: SQLiteRepository, end: datetime, *, name: str = "望月穗波") -> None:
    await repository.set_mysekai_birthday_window(
        character_id=3,
        character_name=name,
        start_ms=int((end - timedelta(hours=72)).timestamp() * 1000),
        end_ms=int(end.timestamp() * 1000),
    )


async def test_enabling_records_the_active_window(tmp_path) -> None:
    service, repository = await _service(tmp_path)
    now = datetime.now(CN)
    service.fetch_birthday_state = AsyncMock(
        return_value=_state(
            active=True,
            start=now - timedelta(hours=1),
            end=now + timedelta(hours=71),
            name="望月穗波",
        )
    )

    result = await service.set_water_watch(QQ, True)

    assert result["enabled"] is True
    assert result["changed"] is True
    assert await repository.is_mysekai_water_watch(QQ) is True
    stored = await repository.get_mysekai_birthday_window()
    assert stored is not None
    assert stored["character_name"] == "望月穗波"


async def test_enabling_twice_keeps_the_first_enabled_at(tmp_path) -> None:
    service, repository = await _service(tmp_path)
    now = datetime.now(CN)
    service.fetch_birthday_state = AsyncMock(
        return_value=_state(
            active=True,
            start=now - timedelta(hours=1),
            end=now + timedelta(hours=71),
            name="望月穗波",
        )
    )

    first = await service.set_water_watch(QQ, True)
    opened_at = await repository.mysekai_water_watch_enabled_at(QQ)
    second = await service.set_water_watch(QQ, True)

    assert first["changed"] is True
    assert second["changed"] is False
    assert await repository.mysekai_water_watch_enabled_at(QQ) == opened_at
    assert service.fetch_birthday_state.await_count == 1


async def test_disabling_when_not_enabled_reports_no_change(tmp_path) -> None:
    service, _ = await _service(tmp_path)

    result = await service.set_water_watch(QQ, False)

    assert result == {"enabled": False, "changed": False, "birthday": None}


async def test_disabling_an_enabled_watch_reports_change(tmp_path) -> None:
    service, repository = await _service(tmp_path)
    await repository.set_mysekai_water_watch(QQ, True)

    result = await service.set_water_watch(QQ, False)

    assert result["changed"] is True
    assert await repository.is_mysekai_water_watch(QQ) is False


async def test_enabling_is_allowed_within_one_hour_before_the_window(tmp_path) -> None:
    service, repository = await _service(tmp_path)
    service.fetch_birthday_state = AsyncMock(
        return_value=_state(
            next_start=datetime.now(CN) + timedelta(minutes=30),
            next_name="天马咲希",
        )
    )

    result = await service.set_water_watch(QQ, True)

    assert result["enabled"] is True
    assert result["changed"] is True
    assert await repository.is_mysekai_water_watch(QQ) is True


async def test_enabling_is_rejected_more_than_one_hour_before_the_window(tmp_path) -> None:
    service, repository = await _service(tmp_path)
    service.fetch_birthday_state = AsyncMock(
        return_value=_state(
            next_start=datetime.now(CN) + timedelta(hours=2),
            next_name="天马咲希",
        )
    )

    with pytest.raises(MysekaiError) as raised:
        await service.set_water_watch(QQ, True)

    assert raised.value.code == "watch_window_closed"
    assert raised.value.status == 409
    assert await repository.is_mysekai_water_watch(QQ) is False


async def test_enabling_is_rejected_when_the_window_cannot_be_checked(tmp_path) -> None:
    service, repository = await _service(tmp_path)
    service.fetch_birthday_state = AsyncMock(return_value=None)

    with pytest.raises(MysekaiError) as raised:
        await service.set_water_watch(QQ, True)

    assert raised.value.code == "birthday_state_unavailable"
    assert raised.value.status == 503
    assert await repository.is_mysekai_water_watch(QQ) is False


async def test_expiry_clears_every_watch_after_the_window(tmp_path) -> None:
    service, repository = await _service(tmp_path)
    await repository.set_mysekai_water_watch(QQ, True)
    await repository.set_mysekai_water_watch(QQ2, True)
    end = datetime.now(CN) - timedelta(minutes=1)
    await _store_window(repository, end)
    service.fetch_birthday_state = AsyncMock(return_value=_state(active=False))

    result = await service.expire_water_watches()

    assert result["cleared"] == 2
    assert result["character_name"] == "望月穗波"
    assert result["start_ms"] is not None
    assert result["end_ms"] is not None
    assert await repository.is_mysekai_water_watch(QQ) is False
    assert await repository.is_mysekai_water_watch(QQ2) is False
    assert await repository.get_mysekai_birthday_window() is None


async def test_expiry_is_idempotent(tmp_path) -> None:
    service, repository = await _service(tmp_path)
    await repository.set_mysekai_water_watch(QQ, True)
    end = datetime.now(CN) - timedelta(minutes=1)
    await _store_window(repository, end)
    service.fetch_birthday_state = AsyncMock(return_value=_state(active=False))

    first = await service.expire_water_watches()
    second = await service.expire_water_watches()

    assert first["cleared"] == 1
    assert second["cleared"] == 0


async def test_expiry_does_not_clear_when_state_is_unavailable(tmp_path) -> None:
    service, repository = await _service(tmp_path)
    await repository.set_mysekai_water_watch(QQ, True)
    await _store_window(repository, datetime.now(CN) - timedelta(minutes=1))
    service.fetch_birthday_state = AsyncMock(return_value=None)

    result = await service.expire_water_watches()

    assert result["cleared"] == 0
    assert await repository.is_mysekai_water_watch(QQ) is True
    assert await repository.get_mysekai_birthday_window() is not None


async def test_expiry_closes_the_old_window_before_recording_a_new_one(tmp_path) -> None:
    service, repository = await _service(tmp_path)
    await repository.set_mysekai_water_watch(QQ, True)
    await _store_window(repository, datetime.now(CN) - timedelta(days=1), name="望月穗波")
    now = datetime.now(CN)
    service.fetch_birthday_state = AsyncMock(
        return_value=_state(
            active=True,
            start=now - timedelta(hours=1),
            end=now + timedelta(hours=71),
            name="天马咲希",
        )
    )

    result = await service.expire_water_watches()

    assert result["cleared"] == 1
    assert result["character_name"] == "望月穗波"
    stored = await repository.get_mysekai_birthday_window()
    assert stored is not None
    assert stored["character_name"] == "天马咲希"


async def test_watch_expire_endpoint_accepts_token_callers_without_origin(tmp_path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    service = app.state.services.mysekai
    async with app.router.lifespan_context(app):
        await service.repository.set_mysekai_water_watch(QQ, True)
        await _store_window(
            service.repository, datetime.now(CN) - timedelta(minutes=1)
        )
        service.fetch_birthday_state = AsyncMock(return_value=_state(active=False))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/internal/v1/mysekai/watch-expire",
                json={},
                headers={"Authorization": f"Bearer {BOT_TOKEN}"},
            )

    assert response.status_code == 200
    assert response.json()["cleared"] == 1
