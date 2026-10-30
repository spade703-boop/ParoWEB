from __future__ import annotations

from pathlib import Path

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


async def test_accelerated_slot_is_not_re_notified(tmp_path) -> None:
    """加速只让 finish_at 变小，同一批不该重新排队再推一次。"""
    repository = await _repository(tmp_path)
    await repository.record_mysekai_convert_slots(GAME, [_slot(count=8)])
    await repository.ack_mysekai_convert_slots(GAME, [SLOT])

    await repository.record_mysekai_convert_slots(GAME, [_slot(count=6)])

    assert await _notified(repository) == 1
    assert await _due(repository) == []


async def test_shortened_minutes_is_not_re_notified(tmp_path) -> None:
    """单件耗时变短（月卡/加速道具）同样只是同一批走得更快。"""
    repository = await _repository(tmp_path)
    await repository.record_mysekai_convert_slots(GAME, [_slot(count=8)])
    await repository.ack_mysekai_convert_slots(GAME, [SLOT])

    await repository.record_mysekai_convert_slots(GAME, [_slot(minutes=30)])

    assert await _notified(repository) == 1
    assert await _due(repository) == []


async def test_maintained_batch_is_not_re_notified(tmp_path) -> None:
    """原样重传同一批也不复活（既有行为，防回归）。"""
    repository = await _repository(tmp_path)
    await repository.record_mysekai_convert_slots(GAME, [_slot()])
    await repository.ack_mysekai_convert_slots(GAME, [SLOT])

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
    await repository.ack_mysekai_convert_slots(GAME, [SLOT])

    await repository.record_mysekai_convert_slots(
        GAME, [_slot(start=START + 8 * 3_600_000, count=1)]
    )

    assert await _notified(repository) == 0
    assert await _due(repository) == [SLOT]
