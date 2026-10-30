"""转换机提醒订阅按角色：迁移、作用范围与自动纳入（2026-10-30）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.repositories.sqlite import SQLiteRepository

QQ = "123456789"
GAME_A = "7440000000000000001"
GAME_B = "7440000000000000002"
STAMP = "2026-10-23T00:00:00Z"
NOW_MS = 1_800_000_000_000


def _slot(slot_id: int, finish: int) -> dict:
    """一槽；record 会按 start + count×minutes 重算 finish。"""
    return {
        "slotId": slot_id,
        "mysekaiConvertItemId": 22,
        "convertStartAt": finish - 3_600_000,
        "onceConvertMinutes": 60,
        "convertCount": 1,
        "userMysekaiConvertObtainResources": [{"seq": 0}],
    }


async def _bind(repository: SQLiteRepository, game_user_id: str, created: str) -> None:
    await repository.create_mysekai_binding(
        qq=QQ,
        server="cn",
        game_user_id=game_user_id,
        nickname=None,
        deck_id=None,
        leader_card_id=None,
        leader_avatar=None,
        profile_image_type=None,
    )


async def _repository(tmp_path: Path) -> SQLiteRepository:
    repository = SQLiteRepository(tmp_path / "test.sqlite3")
    await repository.initialize()
    async with repository.connect() as connection:
        await connection.execute(
            "INSERT INTO mysekai_bindings("
            "qq, server, game_user_id, created_at, updated_at"
            ") VALUES (?, 'cn', ?, ?, ?)",
            (QQ, GAME_A, STAMP, STAMP),
        )
        await connection.execute(
            "INSERT INTO mysekai_bindings("
            "qq, server, game_user_id, created_at, updated_at"
            ") VALUES (?, 'cn', ?, ?, ?)",
            (QQ, GAME_B, STAMP + "1", STAMP + "1"),
        )
        await connection.commit()
    return repository


async def _watched(repository: SQLiteRepository) -> set[str]:
    async with repository.connect() as connection:
        rows = await (
            await connection.execute(
                "SELECT game_user_id FROM mysekai_convert_watches WHERE qq = ?",
                (QQ,),
            )
        ).fetchall()
    return {row["game_user_id"] for row in rows}


async def _due(repository: SQLiteRepository, now_ms: int) -> set[int]:
    rows = await repository.due_mysekai_convert_slots(now_ms)
    return {int(row["slot_id"]) for row in rows}


def _legacy_database(path: Path, *, with_bindings: bool = True) -> None:
    connection = sqlite3.connect(path)
    statements = [
        """
        CREATE TABLE mysekai_bindings (
            qq TEXT NOT NULL, server TEXT NOT NULL, game_user_id TEXT NOT NULL,
            nickname TEXT, deck_id INTEGER, leader_card_id INTEGER,
            leader_avatar TEXT, profile_image_type TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY (qq, server, game_user_id)
        )
        """,
        "CREATE TABLE mysekai_convert_watches (qq TEXT PRIMARY KEY, enabled_at TEXT NOT NULL)",
        f"INSERT INTO mysekai_convert_watches VALUES ('{QQ}', '{STAMP}')",
    ]
    if with_bindings:
        statements.append(
            "INSERT INTO mysekai_bindings("
            "qq, server, game_user_id, created_at, updated_at"
            f") VALUES ('{QQ}', 'cn', '{GAME_A}', '{STAMP}', '{STAMP}')"
        )
        statements.append(
            "INSERT INTO mysekai_bindings("
            "qq, server, game_user_id, created_at, updated_at"
            f") VALUES ('{QQ}', 'cn', '{GAME_B}', '{STAMP}1', '{STAMP}1')"
        )
    connection.executescript(";".join(statements))
    connection.commit()
    connection.close()


async def test_legacy_watch_expands_to_every_bound_role(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite3"
    _legacy_database(database)
    repository = SQLiteRepository(database)

    await repository.initialize()

    assert await _watched(repository) == {GAME_A, GAME_B}


async def test_legacy_watch_without_binding_is_dropped(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite3"
    _legacy_database(database, with_bindings=False)
    repository = SQLiteRepository(database)

    await repository.initialize()

    assert await _watched(repository) == set()


async def test_migration_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite3"
    _legacy_database(database)
    repository = SQLiteRepository(database)
    await repository.initialize()
    await repository.initialize()
    assert await _watched(repository) == {GAME_A, GAME_B}


async def test_subscribing_one_role_does_not_silence_the_other(
    tmp_path: Path,
) -> None:
    import time

    repository = await _repository(tmp_path)
    past = int(time.time() * 1000) - 60_000
    await repository.record_mysekai_convert_slots(GAME_A, [_slot(1, past)])
    await repository.record_mysekai_convert_slots(GAME_B, [_slot(2, past)])

    await repository.set_mysekai_convert_watch(QQ, True, GAME_A)

    async with repository.connect() as connection:
        rows = await (
            await connection.execute(
                "SELECT game_user_id, notified FROM mysekai_convert_slots "
                "WHERE slot_id IN (1, 2)"
            )
        ).fetchall()
    notified = {row["game_user_id"]: int(row["notified"]) for row in rows}
    assert notified[GAME_A] == 1      # 订阅前已完成，静音
    assert notified[GAME_B] == 0      # 另一个角色不该被静音


async def test_due_only_includes_subscribed_roles(tmp_path: Path) -> None:
    repository = await _repository(tmp_path)
    finish = NOW_MS + 3_600_000
    await repository.record_mysekai_convert_slots(GAME_A, [_slot(1, finish)])
    await repository.record_mysekai_convert_slots(GAME_B, [_slot(2, finish)])

    await repository.set_mysekai_convert_watch(QQ, True, GAME_A)

    assert await _due(repository, finish + 60_000) == {1}


async def test_unsubscribing_one_role_keeps_the_other(tmp_path: Path) -> None:
    repository = await _repository(tmp_path)
    finish = NOW_MS + 3_600_000
    await repository.record_mysekai_convert_slots(GAME_A, [_slot(1, finish)])
    await repository.record_mysekai_convert_slots(GAME_B, [_slot(2, finish)])
    await repository.set_mysekai_convert_watch(QQ, True)

    await repository.set_mysekai_convert_watch(QQ, False, GAME_A)

    assert await _watched(repository) == {GAME_B}
    assert await _due(repository, finish + 60_000) == {2}


async def test_a_new_role_joins_an_active_subscription(tmp_path: Path) -> None:
    repository = await _repository(tmp_path)
    await repository.set_mysekai_convert_watch(QQ, True, GAME_A)
    await repository.delete_mysekai_binding(QQ, GAME_B)

    game_c = "7440000000000000003"
    await _bind(repository, game_c, STAMP + "2")

    assert await _watched(repository) == {GAME_A, game_c}


async def test_unbinding_removes_the_role_subscription(tmp_path: Path) -> None:
    repository = await _repository(tmp_path)
    await repository.set_mysekai_convert_watch(QQ, True)

    await repository.delete_mysekai_binding(QQ, GAME_A)

    assert await _watched(repository) == {GAME_B}


async def test_after_unsubscribing_all_a_new_role_is_not_watched(
    tmp_path: Path,
) -> None:
    repository = await _repository(tmp_path)
    await repository.set_mysekai_convert_watch(QQ, True)
    await repository.set_mysekai_convert_watch(QQ, False)
    await repository.delete_mysekai_binding(QQ, GAME_A)
    await repository.delete_mysekai_binding(QQ, GAME_B)

    game_c = "7440000000000000003"
    await _bind(repository, game_c, STAMP + "2")

    assert await _watched(repository) == set()
