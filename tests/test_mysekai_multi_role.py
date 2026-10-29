"""One QQ, several roles: schema migration and repository behaviour (2026-10-29)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.repositories.sqlite import SQLiteRepository


def _legacy_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE mysekai_bindings (
            qq TEXT PRIMARY KEY,
            server TEXT NOT NULL,
            game_user_id TEXT NOT NULL,
            nickname TEXT,
            deck_id INTEGER,
            leader_card_id INTEGER,
            leader_avatar TEXT,
            profile_image_type TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO mysekai_bindings(
            qq, server, game_user_id, nickname, deck_id, leader_card_id,
            leader_avatar, profile_image_type, created_at, updated_at
        ) VALUES (
            '12345', 'cn', '1111111111111111111', '玩家', NULL, NULL,
            NULL, NULL, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'
        );
        """
    )
    connection.commit()
    connection.close()


async def test_legacy_single_binding_becomes_the_current_role(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite3"
    _legacy_database(database)
    repository = SQLiteRepository(database)
    await repository.initialize()

    rows = await repository.list_mysekai_bindings("12345")
    assert len(rows) == 1
    current = await repository.get_mysekai_binding("12345")
    assert current is not None
    assert current["game_user_id"] == "1111111111111111111"


async def test_a_second_role_can_be_added_after_migration(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite3"
    _legacy_database(database)
    repository = SQLiteRepository(database)
    await repository.initialize()

    conflict = await repository.create_mysekai_binding(
        qq="12345",
        server="cn",
        game_user_id="2222222222222222222",
        nickname="小号",
        deck_id=None,
        leader_card_id=None,
        leader_avatar=None,
        profile_image_type=None,
    )
    assert conflict is None
    assert len(await repository.list_mysekai_bindings("12345")) == 2
    # 首次绑定仍是当前角色，新增角色不自动抢占。
    current = await repository.get_mysekai_binding("12345")
    assert current is not None
    assert current["game_user_id"] == "1111111111111111111"


def _interrupted_database(path: Path) -> None:
    """The state a crash between RENAME and DROP would leave behind."""
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE mysekai_bindings_legacy (
            qq TEXT PRIMARY KEY,
            server TEXT NOT NULL,
            game_user_id TEXT NOT NULL,
            nickname TEXT,
            deck_id INTEGER,
            leader_card_id INTEGER,
            leader_avatar TEXT,
            profile_image_type TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO mysekai_bindings_legacy(
            qq, server, game_user_id, nickname, deck_id, leader_card_id,
            leader_avatar, profile_image_type, created_at, updated_at
        ) VALUES (
            '12345', 'cn', '1111111111111111111', '玩家', NULL, NULL,
            NULL, NULL, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'
        );
        CREATE TABLE mysekai_bindings (
            qq TEXT NOT NULL,
            server TEXT NOT NULL,
            game_user_id TEXT NOT NULL,
            nickname TEXT,
            deck_id INTEGER,
            leader_card_id INTEGER,
            leader_avatar TEXT,
            profile_image_type TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (qq, server, game_user_id)
        );
        """
    )
    connection.commit()
    connection.close()


async def test_an_interrupted_migration_is_recovered(tmp_path: Path) -> None:
    database = tmp_path / "interrupted.sqlite3"
    _interrupted_database(database)
    repository = SQLiteRepository(database)

    await repository.initialize()

    rows = await repository.list_mysekai_bindings("12345")
    assert len(rows) == 1
    assert rows[0]["game_user_id"] == "1111111111111111111"
    current = await repository.get_mysekai_binding("12345")
    assert current is not None
    assert current["game_user_id"] == "1111111111111111111"
    connection = sqlite3.connect(database)
    names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    connection.close()
    assert "mysekai_bindings_legacy" not in names


async def test_migration_leaves_a_snapshot_of_the_old_data(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite3"
    _legacy_database(database)
    repository = SQLiteRepository(database)

    await repository.initialize()

    backup = database.with_name(database.name + ".pre-multi-role.bak")
    assert backup.is_file()
    connection = sqlite3.connect(backup)
    rows = connection.execute(
        "SELECT qq, game_user_id FROM mysekai_bindings"
    ).fetchall()
    connection.close()
    assert rows == [("12345", "1111111111111111111")]


async def test_migrating_twice_is_a_no_op(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite3"
    _legacy_database(database)
    repository = SQLiteRepository(database)
    await repository.initialize()
    await repository.initialize()
    assert len(await repository.list_mysekai_bindings("12345")) == 1


async def test_switching_role_changes_the_default(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite3"
    _legacy_database(database)
    repository = SQLiteRepository(database)
    await repository.initialize()
    await repository.create_mysekai_binding(
        qq="12345",
        server="cn",
        game_user_id="2222222222222222222",
        nickname=None,
        deck_id=None,
        leader_card_id=None,
        leader_avatar=None,
        profile_image_type=None,
    )

    assert await repository.set_current_mysekai_role(
        "12345", "cn", "2222222222222222222"
    )
    current = await repository.get_mysekai_binding("12345")
    assert current is not None
    assert current["game_user_id"] == "2222222222222222222"
    # 不属于该 QQ 的角色不能设为当前。
    assert not await repository.set_current_mysekai_role(
        "12345", "cn", "9999999999999999999"
    )


async def test_deleting_the_current_role_falls_back_to_the_other(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy.sqlite3"
    _legacy_database(database)
    repository = SQLiteRepository(database)
    await repository.initialize()
    await repository.create_mysekai_binding(
        qq="12345",
        server="cn",
        game_user_id="2222222222222222222",
        nickname=None,
        deck_id=None,
        leader_card_id=None,
        leader_avatar=None,
        profile_image_type=None,
    )
    await repository.set_current_mysekai_role("12345", "cn", "2222222222222222222")

    removed = await repository.delete_mysekai_binding("12345")
    assert removed is not None
    assert removed["game_user_id"] == "2222222222222222222"
    current = await repository.get_mysekai_binding("12345")
    assert current is not None
    assert current["game_user_id"] == "1111111111111111111"
