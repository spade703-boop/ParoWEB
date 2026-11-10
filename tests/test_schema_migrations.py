from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from app.repositories.sqlite import SQLiteRepository


@pytest.mark.parametrize("state_kind", ["matching", "different_item", "different_finish", "object", "invalid_rows"])
async def test_legacy_database_is_backed_up_and_convert_batch_is_backfilled(
    tmp_path: Path, state_kind: str,
) -> None:
    database = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE mysekai_catalog_state (
            server TEXT NOT NULL,
            game_user_id TEXT NOT NULL,
            music_records TEXT,
            blueprints TEXT,
            fixtures TEXT,
            convert_slots TEXT,
            convert_histories TEXT,
            mysekai_rank INTEGER,
            game_rank INTEGER,
            refreshed_at_ms INTEGER,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (server, game_user_id)
        );
        CREATE TABLE mysekai_convert_slots (
            game_user_id TEXT NOT NULL,
            slot_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL,
            finish_at_ms INTEGER NOT NULL,
            outcome_count INTEGER NOT NULL DEFAULT 0,
            notified INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (game_user_id, slot_id)
        );
        """
    )
    slot = {
        "slotId": 2,
        "mysekaiConvertItemId": 22,
        "convertStartAt": 1792723215029,
        "onceConvertMinutes": 60,
        "convertCount": 8,
        "userMysekaiConvertObtainResources": [{"seq": 0}],
    }
    if state_kind == "different_item":
        slot["mysekaiConvertItemId"] = 99
    if state_kind == "different_finish":
        slot["convertStartAt"] += 1000
    payload = [slot]
    if state_kind == "object":
        payload = {"unexpected": slot}
    if state_kind == "invalid_rows":
        payload = [None, "broken"]
    connection.execute(
        "INSERT INTO mysekai_catalog_state("
        "server, game_user_id, convert_slots, updated_at"
        ") VALUES ('cn', '7440000000000000000', ?, 'old')",
        (json.dumps(payload),),
    )
    connection.execute(
        "INSERT INTO mysekai_convert_slots VALUES "
        "('7440000000000000000', 2, 22, 1792752015029, 1, 1, 'old')"
    )
    connection.commit()
    connection.close()

    repository = SQLiteRepository(database)
    await repository.initialize()

    backups = list(tmp_path.glob("legacy.sqlite3.pre-v11-*.bak"))
    assert len(backups) == 1
    async with repository.connect() as migrated:
        versions = await (
            await migrated.execute("SELECT version FROM schema_migrations ORDER BY version")
        ).fetchall()
        row = await (
            await migrated.execute(
                "SELECT batch_id, batch_start_at_ms, batch_source, slot_payload, notified "
                "FROM mysekai_convert_slots"
            )
        ).fetchone()
    assert [item["version"] for item in versions] == [
        1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11,
    ]
    if state_kind == "matching":
        assert row["batch_id"] == "cn:7440000000000000000:2:1792723215029"
        assert row["batch_start_at_ms"] == 1792723215029
        assert row["batch_source"] == "convert_start_at"
        assert json.loads(row["slot_payload"])["slotId"] == 2
    else:
        assert row["batch_id"].startswith("legacy:cn:")
        assert row["batch_start_at_ms"] is None
        assert row["batch_source"] == "legacy_unknown"
        assert row["slot_payload"] is None
    assert row["notified"] == 1

    await repository.initialize()
    assert len(list(tmp_path.glob("legacy.sqlite3.pre-v11-*.bak"))) == 1


async def test_failed_migration_rolls_back_schema_and_keeps_a_restorable_backup(
    tmp_path: Path, monkeypatch,
) -> None:
    database = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE sentinel(value TEXT)")
        connection.execute("INSERT INTO sentinel VALUES ('keep')")
    repository = SQLiteRepository(database)

    async def fail_backfill(connection):
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(repository, "_backfill_convert_batches", fail_backfill)
    with pytest.raises(RuntimeError, match="injected migration failure"):
        await repository.initialize()

    with sqlite3.connect(database) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        assert tables == [("sentinel",)]
    backups = list(tmp_path.glob("legacy.sqlite3.pre-v*-*.bak"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("SELECT value FROM sentinel").fetchone() == ("keep",)


async def test_v11_removes_the_three_material_constraint(tmp_path: Path) -> None:
    database = tmp_path / "v10.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            );
            CREATE TABLE mysekai_water_watch_materials (
                qq TEXT NOT NULL,
                material_id INTEGER NOT NULL CHECK (material_id IN (5, 12, 20)),
                updated_at TEXT NOT NULL,
                PRIMARY KEY (qq, material_id)
            );
            """
        )
        connection.executemany(
            "INSERT INTO schema_migrations(version, name, applied_at) "
            "VALUES (?, 'old', 'old')",
            [(version,) for version in range(1, 11)],
        )
        connection.execute(
            "INSERT INTO mysekai_water_watch_materials VALUES "
            "('123456789', 12, 'old')"
        )

    repository = SQLiteRepository(database)
    await repository.initialize()
    await repository.replace_mysekai_water_watch_materials(
        "123456789", frozenset({8, 12})
    )

    assert await repository.list_mysekai_water_watch_materials(
        "123456789"
    ) == [8, 12]
