from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

from app.repositories.sqlite import SQLiteRepository


GAME = "7440000000000000000"


async def _repo(tmp_path: Path) -> SQLiteRepository:
    repository = SQLiteRepository(tmp_path / "test.sqlite3")
    await repository.initialize()
    return repository


async def _observation(
    repository: SQLiteRepository,
    observation_id: str,
    version: int | None,
    *,
    raw: object = None,
) -> None:
    await repository.record_mysekai_observation(
        observation_id=observation_id,
        server="cn",
        game_user_id=GAME,
        event_kind="snapshot",
        source_observed_at_ms=version,
        source_time_kind="test" if version is not None else None,
        source_time_raw=raw,
        refreshed_at_ms=None,
        fields_present={"music_records": True},
        body_sha256=observation_id,
    )


async def test_observation_keeps_raw_source_time_and_field_decisions(tmp_path) -> None:
    repository = await _repo(tmp_path)
    await _observation(repository, "new", 200, raw={"clock": 200})
    await repository.set_mysekai_catalog_state(
        server="cn",
        game_user_id=GAME,
        music_records=["new"],
        blueprints=None,
        observation_id="new",
        source_version_ms=200,
    )
    await _observation(repository, "old", 100, raw=100)
    await repository.set_mysekai_catalog_state(
        server="cn",
        game_user_id=GAME,
        music_records=["old"],
        blueprints=None,
        observation_id="old",
        source_version_ms=100,
    )

    async with repository.connect() as connection:
        observation = await (
            await connection.execute(
                "SELECT source_time_raw FROM mysekai_observations "
                "WHERE observation_id = 'new'"
            )
        ).fetchone()
        result = await (
            await connection.execute(
                "SELECT status FROM mysekai_observation_results "
                "WHERE observation_id = 'old' AND state_scope = 'music_records'"
            )
        ).fetchone()
    assert json.loads(observation["source_time_raw"]) == {"clock": 200}
    assert result["status"] == "older_rejected"


async def test_birthday_rejection_is_saved_with_the_observation(tmp_path) -> None:
    repository = await _repo(tmp_path)
    await _observation(repository, "birthday-new", 200)
    await repository.set_mysekai_birthday_state(
        server="cn", game_user_id=GAME, party_id=2, character_id=3,
        obtained=8, dropped=2, total_point=90000, captured_at_ms=200,
        observation_id="birthday-new",
    )
    await _observation(repository, "birthday-old", 100)

    await repository.set_mysekai_birthday_state(
        server="cn", game_user_id=GAME, party_id=2, character_id=3,
        obtained=7, dropped=0, total_point=85260, captured_at_ms=100,
        observation_id="birthday-old",
    )

    async with repository.connect() as connection:
        row = await (
            await connection.execute(
                "SELECT status FROM mysekai_observation_results "
                "WHERE observation_id = 'birthday-old' AND state_scope = 'birthday:2'"
            )
        ).fetchone()
    assert row["status"] == "older_rejected"


async def test_retention_keeps_current_evidence_and_drops_unreferenced_history(
    tmp_path,
) -> None:
    repository = await _repo(tmp_path)
    now = datetime(2026, 10, 27, tzinfo=UTC)
    old = (now - timedelta(days=181)).isoformat().replace("+00:00", "Z")
    await _observation(repository, "current", 200)
    await repository.set_mysekai_catalog_state(
        server="cn", game_user_id=GAME, music_records=["current"], blueprints=None,
        observation_id="current", source_version_ms=200,
    )
    await _observation(repository, "discard", 100)
    await repository.set_mysekai_catalog_state(
        server="cn", game_user_id=GAME, music_records=["old"], blueprints=None,
        observation_id="discard", source_version_ms=100,
    )
    async with repository.connect() as connection:
        await connection.execute(
            "UPDATE mysekai_observations SET received_at = ?", (old,)
        )
        await connection.execute(
            "INSERT INTO mysekai_convert_batches("
            "batch_id, server, game_user_id, slot_id, batch_start_at_ms, "
            "batch_source, updated_at) VALUES ('old-batch', 'cn', ?, 2, 1, 'test', ?)",
            (GAME, old),
        )
        await connection.commit()

    result = await repository.prune_mysekai_history(now)

    async with repository.connect() as connection:
        observations = await (
            await connection.execute(
                "SELECT observation_id FROM mysekai_observations ORDER BY observation_id"
            )
        ).fetchall()
        batch = await (
            await connection.execute(
                "SELECT batch_id FROM mysekai_convert_batches WHERE batch_id = 'old-batch'"
            )
        ).fetchone()
    assert [row["observation_id"] for row in observations] == ["current"]
    assert batch is None
    assert result == {"observations": 1, "convert_batches": 1}
