from __future__ import annotations

from pathlib import Path

from app.repositories.sqlite import SQLiteRepository

GAME = "7440000000000000000"
SLOT = [{"slotId": 2, "mysekaiConvertItemId": 22, "convertCount": 8}]


async def _repo(tmp_path: Path) -> SQLiteRepository:
    repository = SQLiteRepository(tmp_path / "test.sqlite3")
    await repository.initialize()
    return repository


async def test_missing_table_in_a_capture_keeps_what_we_had(tmp_path) -> None:
    """抓包没带这张子表 ≠ 玩家清空了它。"""
    repository = await _repo(tmp_path)
    await repository.set_mysekai_catalog_state(
        server="cn", game_user_id=GAME, music_records=["m1"],
        blueprints=None, fixtures=None, convert_slots=SLOT,
        convert_histories=None, mysekai_rank=12, refreshed_at_ms=1000,
    )

    # 第二次抓包只刷新了唱片，没带槽位
    await repository.set_mysekai_catalog_state(
        server="cn", game_user_id=GAME, music_records=["m2"],
        blueprints=None, fixtures=None, convert_slots=None,
        convert_histories=None, mysekai_rank=None, refreshed_at_ms=None,
    )

    state = await repository.get_mysekai_catalog_state("cn", GAME)
    assert state["convert_slots"] == SLOT
    assert state["mysekai_rank"] == 12
    assert state["refreshed_at_ms"] == 1000
    assert state["music_records"] == ["m2"]


async def test_an_empty_table_still_clears(tmp_path) -> None:
    """「刷新了，一个都没有」必须能写进去。"""
    repository = await _repo(tmp_path)
    await repository.set_mysekai_catalog_state(
        server="cn", game_user_id=GAME, music_records=None,
        blueprints=None, fixtures=None, convert_slots=SLOT,
        convert_histories=None, mysekai_rank=None, refreshed_at_ms=None,
    )

    await repository.set_mysekai_catalog_state(
        server="cn", game_user_id=GAME, music_records=None,
        blueprints=None, fixtures=None, convert_slots=[],
        convert_histories=None, mysekai_rank=None, refreshed_at_ms=None,
    )

    state = await repository.get_mysekai_catalog_state("cn", GAME)
    assert state["convert_slots"] == []
