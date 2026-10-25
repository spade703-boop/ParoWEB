from __future__ import annotations

from pathlib import Path

from app.repositories.sqlite import SQLiteRepository

GAME = "7440000000000000000"


async def _repo(tmp_path: Path) -> SQLiteRepository:
    repository = SQLiteRepository(tmp_path / "test.sqlite3")
    await repository.initialize()
    return repository


async def test_round_trip(tmp_path: Path) -> None:
    repository = await _repo(tmp_path)

    await repository.set_mysekai_birthday_state(
        server="cn",
        game_user_id=GAME,
        party_id=2,
        character_id=3,
        obtained=25,
        dropped=0,
        total_point=241290,
        captured_at_ms=1792911813696,
    )

    row = await repository.get_mysekai_birthday_state("cn", GAME)
    assert row is not None
    assert row["party_id"] == 2
    assert row["character_id"] == 3
    assert row["obtained"] == 25
    assert row["dropped"] == 0
    assert row["total_point"] == 241290
    assert row["captured_at_ms"] == 1792911813696


async def test_upsert_overwrites_the_single_row(tmp_path: Path) -> None:
    repository = await _repo(tmp_path)
    await repository.set_mysekai_birthday_state(
        server="cn", game_user_id=GAME, party_id=2, character_id=3,
        obtained=25, dropped=0, total_point=241290, captured_at_ms=1,
    )

    await repository.set_mysekai_birthday_state(
        server="cn", game_user_id=GAME, party_id=2, character_id=3,
        obtained=26, dropped=0, total_point=250000, captured_at_ms=2,
    )

    row = await repository.get_mysekai_birthday_state("cn", GAME)
    assert row is not None
    assert row["obtained"] == 26
    assert row["total_point"] == 250000
    assert row["captured_at_ms"] == 2


async def test_missing_row_returns_none(tmp_path: Path) -> None:
    repository = await _repo(tmp_path)

    assert await repository.get_mysekai_birthday_state("cn", GAME) is None
