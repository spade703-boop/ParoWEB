from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3

from app.maintenance.cleanup_launch_abuse import (
    CleanupCriteria,
    apply_cleanup,
    build_cleanup_plan,
    connect,
    print_plan,
)
from app.repositories.sqlite import SCHEMA


def insert_batch(
    connection: sqlite3.Connection,
    *,
    batch_id: str,
    visitor_id: str,
    fixed_side: str,
    fixed_name: str | None,
    created_at: str,
    result_count: int,
) -> None:
    connection.execute(
        """
        INSERT INTO draw_batches(id, visitor_id, requested_count, fixed_side, fixed_name, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (batch_id, visitor_id, result_count, fixed_side, fixed_name, created_at),
    )
    connection.executemany(
        """
        INSERT INTO draw_results(
            id, batch_id, position, akito_name, toya_name, is_cooking,
            special_type, special_asset, counts_as_cooking, created_at
        ) VALUES (?, ?, ?, ?, ?, 0, NULL, NULL, 0, ?)
        """,
        [
            (f"{batch_id}-result-{position}", batch_id, position, "彰人", "冬弥", created_at)
            for position in range(1, result_count + 1)
        ],
    )


def create_database(database_path: Path) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.executescript(SCHEMA)
        connection.executemany(
            "INSERT INTO visitors(id, token_hash, created_at, last_seen_at) VALUES (?, ?, ?, ?)",
            [
                ("flagged", "flagged-hash", "2026-09-24T00:00:00Z", "2026-09-26T00:00:00Z"),
                ("normal", "normal-hash", "2026-09-24T00:00:00Z", "2026-09-26T00:00:00Z"),
            ],
        )
        insert_batch(
            connection,
            batch_id="flagged-1",
            visitor_id="flagged",
            fixed_side="akito",
            fixed_name="固定彰人",
            created_at="2026-09-26T01:10:00Z",
            result_count=3,
        )
        insert_batch(
            connection,
            batch_id="flagged-2",
            visitor_id="flagged",
            fixed_side="akito",
            fixed_name="固定彰人",
            created_at="2026-09-26T01:20:00Z",
            result_count=2,
        )
        insert_batch(
            connection,
            batch_id="different-name",
            visitor_id="flagged",
            fixed_side="akito",
            fixed_name="另一个彰人",
            created_at="2026-09-26T01:30:00Z",
            result_count=2,
        )
        insert_batch(
            connection,
            batch_id="random",
            visitor_id="flagged",
            fixed_side="none",
            fixed_name=None,
            created_at="2026-09-26T01:40:00Z",
            result_count=3,
        )
        insert_batch(
            connection,
            batch_id="normal-fixed",
            visitor_id="normal",
            fixed_side="toya",
            fixed_name="固定冬弥",
            created_at="2026-09-26T01:50:00Z",
            result_count=2,
        )
        insert_batch(
            connection,
            batch_id="outside-window",
            visitor_id="flagged",
            fixed_side="akito",
            fixed_name="固定彰人",
            created_at="2026-09-28T01:10:00Z",
            result_count=3,
        )
        connection.commit()


def criteria() -> CleanupCriteria:
    return CleanupCriteria(
        start="2026-09-24T00:00:00Z",
        end="2026-09-27T00:00:00Z",
        peak_results_per_hour=5,
        min_total_results=5,
        min_span_minutes=10,
    )


def test_cleanup_plan_only_flags_same_fixed_group_inside_window(tmp_path: Path) -> None:
    database_path = tmp_path / "draws.sqlite3"
    create_database(database_path)

    with connect(database_path, read_only=True) as connection:
        plan = build_cleanup_plan(connection, criteria())

    assert plan.visitor_count == 1
    assert len(plan.groups) == 1
    assert plan.groups[0].visitor_id == "flagged"
    assert plan.groups[0].result_count == 5
    assert plan.groups[0].peak_results_per_hour == 5
    assert {batch.id for batch in plan.batches} == {"flagged-1", "flagged-2"}
    assert plan.result_count == 5


def test_cleanup_plan_preserves_short_burst_even_when_volume_matches(tmp_path: Path) -> None:
    database_path = tmp_path / "draws.sqlite3"
    create_database(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO visitors(id, token_hash, created_at, last_seen_at) VALUES (?, ?, ?, ?)",
            ("short", "short-hash", "2026-09-26T00:00:00Z", "2026-09-26T00:00:00Z"),
        )
        insert_batch(
            connection,
            batch_id="short-1",
            visitor_id="short",
            fixed_side="akito",
            fixed_name="短时固定彰人",
            created_at="2026-09-26T02:00:00Z",
            result_count=3,
        )
        insert_batch(
            connection,
            batch_id="short-2",
            visitor_id="short",
            fixed_side="akito",
            fixed_name="短时固定彰人",
            created_at="2026-09-26T02:02:00Z",
            result_count=2,
        )
        connection.commit()

    with connect(database_path, read_only=True) as connection:
        plan = build_cleanup_plan(connection, criteria())

    assert {group.visitor_id for group in plan.groups} == {"flagged"}
    assert {batch.id for batch in plan.batches} == {"flagged-1", "flagged-2"}


def test_preview_displays_selected_name_time_range_and_counts(tmp_path: Path, capsys) -> None:
    database_path = tmp_path / "draws.sqlite3"
    create_database(database_path)
    with connect(database_path, read_only=True) as connection:
        plan = build_cleanup_plan(connection, criteria())

    print_plan(plan, database_path)

    output = capsys.readouterr().out
    assert "按北京时间日期汇总：2026-09-26：5 条" in output
    assert "固定选择：彰人方 · 固定彰人" in output
    assert "记录时间（北京时间）：2026-09-26 09:10:00 ～ 2026-09-26 09:20:00" in output
    assert "抽取结果：5 条；抽取批次：2 次；首尾跨度：10 分钟；单个 UTC 整点小时峰值：5 条" in output


def test_apply_cleanup_backs_up_audits_and_preserves_cookie_mapping(tmp_path: Path) -> None:
    database_path = tmp_path / "draws.sqlite3"
    backup_path = tmp_path / "before.sqlite3"
    manifest_path = tmp_path / "cleanup.json"
    create_database(database_path)
    with connect(database_path, read_only=True) as connection:
        plan = build_cleanup_plan(connection, criteria())

    result = apply_cleanup(
        database_path,
        plan,
        backup_path=backup_path,
        manifest_path=manifest_path,
        run_at=datetime(2026, 10, 18, tzinfo=UTC),
    )

    assert result == {"deleted_batches": 2, "deleted_results": 5}
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM visitors WHERE id = 'flagged'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM draw_batches").fetchone()[0] == 4
        assert connection.execute("SELECT COUNT(*) FROM draw_results").fetchone()[0] == 10
        assert connection.execute(
            "SELECT COUNT(*) FROM draw_batches WHERE id IN ('different-name', 'random', 'outside-window')"
        ).fetchone()[0] == 3
    with sqlite3.connect(backup_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM draw_batches").fetchone()[0] == 6
        assert connection.execute("SELECT COUNT(*) FROM draw_results").fetchone()[0] == 15

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["summary"] == {
        "visitor_count": 1,
        "group_count": 1,
        "batch_count": 2,
        "result_count": 5,
        "results_by_china_date": {"2026-09-26": 5},
    }
    assert set(manifest["batch_ids"]) == {"flagged-1", "flagged-2"}
    assert manifest["backup_sha256"]
