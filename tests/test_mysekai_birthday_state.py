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


async def test_newer_observation_replaces_the_whole_birthday_tuple(tmp_path: Path) -> None:
    repository = await _repo(tmp_path)
    await repository.set_mysekai_birthday_state(
        server="cn", game_user_id=GAME, party_id=2, character_id=3,
        obtained=25, dropped=0, total_point=241290, captured_at_ms=1,
    )

    await repository.set_mysekai_birthday_state(
        server="cn", game_user_id=GAME, party_id=2, character_id=3,
        obtained=24, dropped=3, total_point=250000, captured_at_ms=2,
    )

    row = await repository.get_mysekai_birthday_state("cn", GAME)
    assert row is not None
    assert (row["obtained"], row["dropped"], row["total_point"]) == (
        24,
        3,
        250000,
    )


async def test_older_observation_cannot_mix_birthday_fields(tmp_path: Path) -> None:
    repository = await _repo(tmp_path)
    await repository.set_mysekai_birthday_state(
        server="cn", game_user_id=GAME, party_id=2, character_id=3,
        obtained=8, dropped=2, total_point=90000, captured_at_ms=200,
    )

    status = await repository.set_mysekai_birthday_state(
        server="cn", game_user_id=GAME, party_id=2, character_id=3,
        obtained=7, dropped=0, total_point=85260, captured_at_ms=100,
    )

    row = await repository.get_mysekai_birthday_state("cn", GAME)
    assert status == "older_rejected"
    assert row is not None
    assert (row["obtained"], row["dropped"], row["total_point"]) == (
        8,
        2,
        90000,
    )


async def test_simultaneous_parties_keep_independent_progress(tmp_path: Path) -> None:
    repository = await _repo(tmp_path)
    await repository.set_mysekai_birthday_state(
        server="cn", game_user_id=GAME, party_id=2, character_id=3,
        obtained=25, dropped=0, total_point=241290, captured_at_ms=1,
    )

    await repository.set_mysekai_birthday_state(
        server="cn", game_user_id=GAME, party_id=3, character_id=49,
        obtained=1, dropped=0, total_point=10000, captured_at_ms=2,
    )

    rows = await repository.list_mysekai_birthday_states("cn", GAME)
    assert {(row["party_id"], row["obtained"]) for row in rows} == {
        (2, 25),
        (3, 1),
    }


async def test_versions_are_compared_within_one_party_only(tmp_path: Path) -> None:
    repository = await _repo(tmp_path)
    await repository.set_mysekai_birthday_state(
        server="cn", game_user_id=GAME, party_id=2, character_id=3,
        obtained=25, dropped=0, total_point=241290, captured_at_ms=200,
    )

    status = await repository.set_mysekai_birthday_state(
        server="cn", game_user_id=GAME, party_id=3, character_id=4,
        obtained=1, dropped=0, total_point=10000, captured_at_ms=100,
    )

    assert status == "accepted"
    assert await repository.get_mysekai_birthday_state("cn", GAME, 3) is not None


async def test_trusted_game_time_replaces_legacy_unlabeled_time(tmp_path: Path) -> None:
    repository = await _repo(tmp_path)
    async with repository.connect() as connection:
        await connection.execute(
            """
            INSERT INTO mysekai_birthday_state(
                server, game_user_id, party_id, character_id,
                obtained, dropped, total_point, captured_at_ms, updated_at
            ) VALUES ('cn', ?, 2, 3, 7, 0, 85260, 500, 'legacy')
            """,
            (GAME,),
        )
        await connection.commit()

    status = await repository.set_mysekai_birthday_state(
        server="cn", game_user_id=GAME, party_id=2, character_id=3,
        obtained=8, dropped=0, total_point=90000, captured_at_ms=100,
        captured_at_source="updatedResources.now:unix_milliseconds",
        observation_id="observation-1",
    )

    row = await repository.get_mysekai_birthday_state("cn", GAME)
    assert status == "accepted"
    assert row is not None
    assert row["obtained"] == 8
    assert row["captured_at_source"] == (
        "updatedResources.now:unix_milliseconds"
    )
