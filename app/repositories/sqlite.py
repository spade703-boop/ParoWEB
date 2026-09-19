from __future__ import annotations

from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
import uuid

import aiosqlite

from app.domain.models import DrawResult, FixedSide


SCHEMA = """
CREATE TABLE IF NOT EXISTS visitors (
    id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS draw_batches (
    id TEXT PRIMARY KEY,
    visitor_id TEXT NOT NULL REFERENCES visitors(id) ON DELETE CASCADE,
    requested_count INTEGER NOT NULL CHECK (requested_count BETWEEN 1 AND 3),
    fixed_side TEXT NOT NULL CHECK (fixed_side IN ('none', 'akito', 'toya')),
    fixed_name TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS draw_results (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES draw_batches(id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position BETWEEN 1 AND 3),
    akito_name TEXT,
    toya_name TEXT,
    is_cooking INTEGER NOT NULL,
    special_type TEXT,
    counts_as_cooking INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(batch_id, position)
);

CREATE INDEX IF NOT EXISTS idx_batches_visitor_created
    ON draw_batches(visitor_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_results_batch_position
    ON draw_results(batch_id, position);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class SQLiteRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    @asynccontextmanager
    async def connect(self):
        connection = await aiosqlite.connect(self.database_path)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA foreign_keys = ON")
        await connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            await connection.close()

    async def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        async with self.connect() as connection:
            await connection.execute("PRAGMA journal_mode = WAL")
            await connection.executescript(SCHEMA)
            await connection.commit()

    async def healthcheck(self) -> bool:
        try:
            async with self.connect() as connection:
                row = await (await connection.execute("SELECT 1 AS ok")).fetchone()
                return bool(row and row["ok"] == 1)
        except Exception:
            return False

    async def get_or_create_visitor(self, token_hash: str) -> dict:
        now = utc_now()
        async with self.connect() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            row = await (
                await connection.execute(
                    "SELECT id, created_at FROM visitors WHERE token_hash = ?",
                    (token_hash,),
                )
            ).fetchone()
            if row:
                await connection.execute(
                    "UPDATE visitors SET last_seen_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
                visitor = {"id": row["id"], "created_at": row["created_at"]}
            else:
                visitor = {"id": str(uuid.uuid4()), "created_at": now}
                await connection.execute(
                    "INSERT INTO visitors(id, token_hash, created_at, last_seen_at) VALUES (?, ?, ?, ?)",
                    (visitor["id"], token_hash, now, now),
                )
            await connection.commit()
            return visitor

    async def create_draw(
        self,
        *,
        visitor_id: str,
        requested_count: int,
        fixed_side: FixedSide,
        fixed_name: str | None,
        results: list[DrawResult],
    ) -> tuple[str, str]:
        batch_id = str(uuid.uuid4())
        created_at = utc_now()
        async with self.connect() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            try:
                await connection.execute(
                    """
                    INSERT INTO draw_batches(id, visitor_id, requested_count, fixed_side, fixed_name, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (batch_id, visitor_id, requested_count, fixed_side, fixed_name, created_at),
                )
                await connection.executemany(
                    """
                    INSERT INTO draw_results(
                        id, batch_id, position, akito_name, toya_name, is_cooking,
                        special_type, counts_as_cooking, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            str(uuid.uuid4()),
                            batch_id,
                            position,
                            result.akito_name,
                            result.toya_name,
                            int(result.is_cooking),
                            result.special_type,
                            int(result.counts_as_cooking),
                            created_at,
                        )
                        for position, result in enumerate(results, 1)
                    ],
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        return batch_id, created_at

    async def profile(self, visitor_id: str, *, recent_limit: int = 50) -> dict:
        async with self.connect() as connection:
            rows = await (
                await connection.execute(
                    """
                    SELECT r.id, r.position, r.akito_name, r.toya_name, r.is_cooking,
                           r.special_type, r.counts_as_cooking, r.created_at,
                           b.id AS batch_id, b.requested_count, b.fixed_side, b.fixed_name
                    FROM draw_results r
                    JOIN draw_batches b ON b.id = r.batch_id
                    WHERE b.visitor_id = ?
                    ORDER BY r.created_at DESC, b.rowid DESC, r.position DESC
                    """,
                    (visitor_id,),
                )
            ).fetchall()

        draw_count = len(rows)
        cooking_count = sum(int(row["counts_as_cooking"]) for row in rows)
        specials = Counter(row["special_type"] for row in rows if row["special_type"])
        normal_rows = [row for row in rows if not row["special_type"]]
        akito_hits = Counter(row["akito_name"] for row in normal_rows if row["akito_name"])
        toya_hits = Counter(row["toya_name"] for row in normal_rows if row["toya_name"])
        pair_hits = Counter(
            (row["akito_name"], row["toya_name"])
            for row in normal_rows
            if row["akito_name"] and row["toya_name"]
        )
        rank_order: dict[tuple[str, str], int] = {}
        akito_order: dict[str, int] = {}
        toya_order: dict[str, int] = {}
        for index, row in enumerate(rows):
            if row["special_type"]:
                continue
            akito_order.setdefault(row["akito_name"], index)
            toya_order.setdefault(row["toya_name"], index)
            rank_order.setdefault((row["akito_name"], row["toya_name"]), index)

        def top_names(counter: Counter, order: dict, limit: int = 3) -> list[dict]:
            values = sorted(counter.items(), key=lambda item: (-item[1], order.get(item[0], 10**9), str(item[0])))
            return [{"name": name, "count": count} for name, count in values[:limit]]

        pair_values = sorted(pair_hits.items(), key=lambda item: (-item[1], rank_order.get(item[0], 10**9), item[0]))
        return {
            "draw_count": draw_count,
            "cooking_count": cooking_count,
            "special_counts": dict(sorted(specials.items())),
            "akito_top": top_names(akito_hits, akito_order),
            "toya_top": top_names(toya_hits, toya_order),
            "pair_top": [
                {"akito_name": pair[0], "toya_name": pair[1], "count": count}
                for pair, count in pair_values[:3]
            ],
            "recent": [dict(row) for row in rows[:recent_limit]],
        }

    async def clear_history(self, visitor_id: str) -> None:
        async with self.connect() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            try:
                await connection.execute("DELETE FROM visitors WHERE id = ?", (visitor_id,))
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
