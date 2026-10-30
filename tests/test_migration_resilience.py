"""迁移健壮性：失败不阻塞启动、缓存表直接重建（2026-10-30 线上事故）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.repositories.sqlite import SQLiteRepository


def _legacy_capture_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE mysekai_latest_capture (
            qq TEXT PRIMARY KEY,
            game_user_id TEXT NOT NULL,
            upload_id TEXT NOT NULL,
            captured_at_ms INTEGER,
            uploaded_at_ms INTEGER,
            map_png BLOB NOT NULL,
            card_png BLOB NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO mysekai_latest_capture VALUES (
            '12345', '1111111111111111111', 'u1', 1, 1, x'00', x'00',
            '2026-10-30T00:00:00Z'
        );
        """
    )
    connection.commit()
    connection.close()


async def test_latest_capture_is_rebuilt_not_copied(tmp_path: Path) -> None:
    """缓存表转移只重建结构，不搬数据——181MB 的整表复制曾把磁盘写爆。"""
    database = tmp_path / "legacy.sqlite3"
    _legacy_capture_database(database)
    repository = SQLiteRepository(database)

    await repository.initialize()

    async with repository.connect() as connection:
        columns = await (
            await connection.execute("PRAGMA table_info(mysekai_latest_capture)")
        ).fetchall()
        count = await (
            await connection.execute("SELECT COUNT(*) FROM mysekai_latest_capture")
        ).fetchone()
    primary = {row["name"] for row in columns if int(row["pk"] or 0)}
    assert primary == {"qq", "game_user_id"}
    assert count[0] == 0


async def test_a_failing_migration_does_not_block_startup(
    tmp_path: Path, monkeypatch
) -> None:
    repository = SQLiteRepository(tmp_path / "test.sqlite3")

    async def boom(connection) -> None:
        raise RuntimeError("database or disk is full")

    monkeypatch.setattr(repository, "_migrate_role_scoped_caches", boom)

    await repository.initialize()

    # 启动没有被迁移异常打断，普通读写仍然可用。
    assert await repository.list_mysekai_bindings("12345") == []


def test_snapshot_is_skipped_when_disk_is_tight(tmp_path: Path, monkeypatch) -> None:
    repository = SQLiteRepository(tmp_path / "test.sqlite3")
    (tmp_path / "test.sqlite3").write_bytes(b"x" * 1000)

    class Usage:
        free = 1024  # 远小于「库大小 × 2 + 64MB」

    monkeypatch.setattr(
        "app.repositories.sqlite.shutil.disk_usage", lambda path: Usage()
    )
    assert repository._has_room_for_snapshot() is False
