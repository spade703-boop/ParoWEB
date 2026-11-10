from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from app.repositories.sqlite import SQLiteRepository

GAME = "7440000000000000000"
QQ = "123456789"
SLOT = 2
START = 1_792_723_215_029
# 远晚于所有 finish_at，due 只看存储值，避免依赖真实时钟。
NOW_MS = 1_800_000_000_000
STAMP = "2026-10-23T00:00:00Z"


def _slot(*, start: int = START, minutes: int = 60, count: int = 8) -> dict:
    """抓包里的一个槽位；finish = start + count × minutes × 60000。"""
    return {
        "slotId": SLOT,
        "mysekaiConvertItemId": 22,
        "convertStartAt": start,
        "onceConvertMinutes": minutes,
        "convertCount": count,
        "userMysekaiConvertObtainResources": [{"seq": i} for i in range(count)],
    }


async def _repository(tmp_path: Path) -> SQLiteRepository:
    repository = SQLiteRepository(tmp_path / "test.sqlite3")
    await repository.initialize()
    async with repository.connect() as connection:
        await connection.execute(
            "INSERT INTO mysekai_bindings("
            "qq, server, game_user_id, created_at, updated_at"
            ") VALUES (?, 'cn', ?, ?, ?)",
            (QQ, GAME, STAMP, STAMP),
        )
        await connection.execute(
            "INSERT INTO mysekai_convert_watches(qq, game_user_id, enabled_at) "
            "VALUES (?, ?, ?)",
            (QQ, GAME, STAMP),
        )
        await connection.commit()
    return repository


async def _notified(repository: SQLiteRepository) -> int:
    async with repository.connect() as connection:
        row = await (
            await connection.execute(
                "SELECT notified FROM mysekai_convert_slots "
                "WHERE game_user_id = ? AND slot_id = ?",
                (GAME, SLOT),
            )
        ).fetchone()
    return -1 if row is None else int(row["notified"])


async def _due(repository: SQLiteRepository) -> list[int]:
    return [
        int(row["slot_id"])
        for row in await repository.due_mysekai_convert_slots(NOW_MS)
    ]


async def _batch(repository: SQLiteRepository) -> dict:
    rows = await repository.due_mysekai_convert_slots(NOW_MS)
    row = rows[0]
    return {
        "batch_id": row["batch_id"],
        "server": row["server"],
        "slot_id": row["slot_id"],
        "convert_start_at_ms": row["convert_start_at_ms"],
    }


async def test_accelerated_slot_is_not_re_notified(tmp_path) -> None:
    """加速只让 finish_at 变小，同一批不该重新排队再推一次。"""
    repository = await _repository(tmp_path)
    await repository.record_mysekai_convert_slots(GAME, [_slot(count=8)])
    await repository.ack_mysekai_convert_slots(GAME, [await _batch(repository)])

    await repository.record_mysekai_convert_slots(GAME, [_slot(count=6)])

    assert await _notified(repository) == 1
    assert await _due(repository) == []


async def test_shortened_minutes_is_not_re_notified(tmp_path) -> None:
    """单件耗时变短（月卡/加速道具）同样只是同一批走得更快。"""
    repository = await _repository(tmp_path)
    await repository.record_mysekai_convert_slots(GAME, [_slot(count=8)])
    await repository.ack_mysekai_convert_slots(GAME, [await _batch(repository)])

    await repository.record_mysekai_convert_slots(GAME, [_slot(minutes=30)])

    assert await _notified(repository) == 1
    assert await _due(repository) == []


async def test_maintained_batch_is_not_re_notified(tmp_path) -> None:
    """原样重传同一批也不复活（既有行为，防回归）。"""
    repository = await _repository(tmp_path)
    await repository.record_mysekai_convert_slots(GAME, [_slot()])
    await repository.ack_mysekai_convert_slots(GAME, [await _batch(repository)])

    await repository.record_mysekai_convert_slots(GAME, [_slot()])

    assert await _notified(repository) == 1
    assert await _due(repository) == []


async def test_pending_batch_after_acceleration_still_notifies(tmp_path) -> None:
    """还没通知的批次被加速，按新的完成时间照常推一次。"""
    repository = await _repository(tmp_path)
    await repository.record_mysekai_convert_slots(GAME, [_slot(count=8)])

    await repository.record_mysekai_convert_slots(GAME, [_slot(count=6)])

    assert await _notified(repository) == 0
    assert await _due(repository) == [SLOT]


async def test_new_batch_after_completion_is_queued(tmp_path) -> None:
    """领完后开新一批（start 前进、完成更晚）必须重新排队。"""
    repository = await _repository(tmp_path)
    await repository.record_mysekai_convert_slots(GAME, [_slot(count=8)])
    await repository.ack_mysekai_convert_slots(GAME, [await _batch(repository)])

    await repository.record_mysekai_convert_slots(
        GAME, [_slot(start=START + 8 * 3_600_000, count=1)]
    )

    assert await _notified(repository) == 0
    assert await _due(repository) == [SLOT]


async def test_older_batch_replay_cannot_replace_new_batch(tmp_path) -> None:
    repository = await _repository(tmp_path)
    newer_start = START + 8 * 3_600_000
    await repository.record_mysekai_convert_slots(
        GAME, [_slot(start=newer_start, count=1)]
    )

    await repository.record_mysekai_convert_slots(GAME, [_slot(start=START, count=8)])

    rows = await repository.due_mysekai_convert_slots(NOW_MS)
    assert rows[0]["convert_start_at_ms"] == newer_start


async def test_old_ack_cannot_confirm_the_new_batch(tmp_path) -> None:
    repository = await _repository(tmp_path)
    await repository.record_mysekai_convert_slots(GAME, [_slot(count=8)])
    old_batch = await _batch(repository)
    await repository.record_mysekai_convert_slots(
        GAME, [_slot(start=START + 8 * 3_600_000, count=1)]
    )

    result = await repository.ack_mysekai_convert_slots(GAME, [old_batch])

    assert result == {"acked": [], "expired": [old_batch["batch_id"]]}
    assert await _notified(repository) == 0


async def test_due_batch_keeps_its_own_payload_after_slot_restarts(tmp_path) -> None:
    repository = await _repository(tmp_path)
    await repository.record_mysekai_convert_slots(GAME, [_slot(count=8)])
    old_batch = await _batch(repository)
    await repository.record_mysekai_convert_slots(
        GAME, [_slot(start=START + 8 * 3_600_000, count=1)]
    )

    slots = await repository.get_mysekai_convert_batch_slots(GAME, [old_batch])

    assert slots is not None
    assert slots[0]["convertStartAt"] == START
    assert slots[0]["convertCount"] == 8


async def test_older_same_batch_progress_cannot_undo_acceleration(tmp_path) -> None:
    repository = await _repository(tmp_path)
    await repository.record_mysekai_convert_slots(
        GAME, [_slot(count=8)], observed_at_ms=100
    )
    await repository.record_mysekai_convert_slots(
        GAME, [_slot(count=6)], observed_at_ms=200
    )

    await repository.record_mysekai_convert_slots(
        GAME, [_slot(count=8)], observed_at_ms=150
    )

    batch = await _batch(repository)
    slots = await repository.get_mysekai_convert_batch_slots(GAME, [batch])
    assert slots is not None
    assert slots[0]["convertCount"] == 6


async def test_newer_empty_slot_table_removes_current_batch(tmp_path) -> None:
    repository = await _repository(tmp_path)
    await repository.record_mysekai_convert_slots(
        GAME, [_slot()], observed_at_ms=START + 1
    )

    await repository.record_mysekai_convert_slots(
        GAME, [], observed_at_ms=START + 2
    )

    assert await _due(repository) == []


async def test_older_empty_slot_table_keeps_newer_batch(tmp_path) -> None:
    repository = await _repository(tmp_path)
    await repository.record_mysekai_convert_slots(
        GAME, [_slot()], observed_at_ms=START + 2
    )

    await repository.record_mysekai_convert_slots(
        GAME, [], observed_at_ms=START - 1
    )

    assert await _due(repository) == [SLOT]


async def test_equal_observation_cannot_change_the_same_batch(tmp_path) -> None:
    repository = await _repository(tmp_path)
    await repository.record_mysekai_convert_slots(
        GAME, [_slot(count=8)], observed_at_ms=START + 1
    )
    batch = await _batch(repository)

    await repository.record_mysekai_convert_slots(
        GAME, [_slot(count=6)], observed_at_ms=START + 1
    )

    slots = await repository.get_mysekai_convert_batch_slots(GAME, [batch])
    assert slots[0]["convertCount"] == 8


async def test_cleared_slot_cannot_be_revived_by_an_older_snapshot(tmp_path) -> None:
    repository = await _repository(tmp_path)
    await repository.record_mysekai_convert_slots(
        GAME, [_slot()], observed_at_ms=START + 1
    )
    await repository.record_mysekai_convert_slots(
        GAME, [], observed_at_ms=START + 3
    )

    await repository.record_mysekai_convert_slots(
        GAME, [_slot()], observed_at_ms=START + 2
    )

    assert await _due(repository) == []
    state = await repository.get_mysekai_catalog_state("cn", GAME)
    assert state["convert_slots"] == []


async def test_older_empty_table_cannot_erase_newer_same_batch_progress(tmp_path) -> None:
    repository = await _repository(tmp_path)
    await repository.record_mysekai_convert_slots(
        GAME, [_slot(count=6)], observed_at_ms=START + 3
    )

    await repository.record_mysekai_convert_slots(
        GAME, [], observed_at_ms=START + 2
    )

    assert await _due(repository) == [SLOT]


async def test_catalog_and_reminders_rollback_together(tmp_path) -> None:
    repository = await _repository(tmp_path)
    await repository.record_mysekai_convert_slots(
        GAME, [_slot()], observed_at_ms=START + 1
    )
    async with repository.connect() as connection:
        await connection.execute(
            "CREATE TRIGGER fail_convert_update BEFORE UPDATE ON mysekai_convert_slots "
            "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
        )
        await connection.commit()

    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        await repository.record_mysekai_convert_slots(
            GAME, [_slot(count=6)], observed_at_ms=START + 2
        )

    state = await repository.get_mysekai_catalog_state("cn", GAME)
    assert state["convert_slots"][0]["convertCount"] == 8
    batch = await _batch(repository)
    slots = await repository.get_mysekai_convert_batch_slots(GAME, [batch])
    assert slots[0]["convertCount"] == 8


async def test_regions_keep_independent_slots_for_the_same_numeric_account(tmp_path) -> None:
    repository = await _repository(tmp_path)
    async with repository.connect() as connection:
        await connection.execute(
            "INSERT INTO mysekai_bindings(qq, server, game_user_id, created_at, updated_at) "
            "VALUES ('another-qq', 'jp', ?, ?, ?)",
            (GAME, STAMP, STAMP),
        )
        await connection.execute(
            "INSERT INTO mysekai_convert_watches(qq, enabled_at) VALUES ('another-qq', ?)",
            (STAMP,),
        )
        await connection.commit()
    await repository.record_mysekai_convert_slots(GAME, [_slot()], server="cn")
    await repository.record_mysekai_convert_slots(GAME, [_slot(count=1)], server="jp")

    rows = await repository.due_mysekai_convert_slots(NOW_MS)

    assert {row["server"] for row in rows} == {"cn", "jp"}
    chinese = next(row for row in rows if row["server"] == "cn")
    await repository.ack_mysekai_convert_slots(GAME, [chinese])
    assert [row["server"] for row in await repository.due_mysekai_convert_slots(NOW_MS)] == ["jp"]
