from __future__ import annotations

from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone
import json
import math
from pathlib import Path
import uuid

import aiosqlite

from app.domain.models import DrawLimitError, DrawResult, FixedSide


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
    special_asset TEXT,
    counts_as_cooking INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(batch_id, position)
);

CREATE TABLE IF NOT EXISTS draw_limits (
    visitor_id TEXT PRIMARY KEY REFERENCES visitors(id) ON DELETE CASCADE,
    last_draw_at TEXT,
    quota_day TEXT NOT NULL,
    daily_results INTEGER NOT NULL DEFAULT 0 CHECK (daily_results >= 0)
);

CREATE INDEX IF NOT EXISTS idx_batches_visitor_created
    ON draw_batches(visitor_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_results_batch_position
    ON draw_results(batch_id, position);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


CHINA_TZ = timezone(timedelta(hours=8))


# 每日累计结果条数下限 -> 冷却秒数，按顺序命中最后满足的一项。
# 5 秒起步，相邻两档最多涨 2 倍；700 条之后进入高位墙。
COOLDOWN_TIERS: tuple[tuple[int, int], ...] = (
    (100, 10),
    (200, 20),
    (300, 30),
    (400, 45),
    (500, 60),
    (700, 120),
    (900, 300),
)


def cooldown_for_daily_results(daily_results: int, base_seconds: int = 5) -> int:
    if base_seconds <= 0:
        return 0
    seconds = base_seconds
    for threshold, tier_seconds in COOLDOWN_TIERS:
        if daily_results < threshold:
            break
        seconds = tier_seconds
    return seconds


def _with_decoded_asset(row) -> dict:
    item = dict(row)
    item["special_asset"] = decode_special_asset(item.get("special_asset"))
    return item


def encode_special_asset(stems: tuple[str | None, ...]) -> str | None:
    """记录里存本的图 stem 列表；全空（老记录 / 没有候选图）就存 NULL。"""
    return json.dumps(list(stems), ensure_ascii=False) if any(stems) else None


def decode_special_asset(raw: object) -> tuple[str | None, ...]:
    if not isinstance(raw, str) or not raw:
        return ()
    try:
        value = json.loads(raw)
    except ValueError:
        return ()
    return tuple(value) if isinstance(value, list) else ()


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
            # CREATE TABLE IF NOT EXISTS 不会给已有库补列，老库要靠这里补上。
            # 用 PRAGMA 判断而不是吞 duplicate column 异常：并发下吞异常会把真正的
            # SQLITE_READONLY 一起吞掉，以后没法排查。
            columns = {
                row["name"]
                for row in (
                    await (
                        await connection.execute("PRAGMA table_info(draw_results)")
                    ).fetchall()
                )
            }
            if "special_asset" not in columns:
                await connection.execute(
                    "ALTER TABLE draw_results ADD COLUMN special_asset TEXT"
                )
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
        cooldown_seconds: int = 5,
    ) -> tuple[str, str, int]:
        batch_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        created_at = now.isoformat().replace("+00:00", "Z")
        quota_day = now.astimezone(CHINA_TZ).date().isoformat()
        result_count = len(results)
        async with self.connect() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            try:
                await connection.execute(
                    """
                    INSERT INTO draw_limits(visitor_id, quota_day, daily_results)
                    VALUES (?, ?, 0)
                    ON CONFLICT(visitor_id) DO NOTHING
                    """,
                    (visitor_id, quota_day),
                )
                limit_row = await (
                    await connection.execute(
                        "SELECT last_draw_at, quota_day, daily_results FROM draw_limits WHERE visitor_id = ?",
                        (visitor_id,),
                    )
                ).fetchone()
                daily_results = int(limit_row["daily_results"])
                last_draw_at = limit_row["last_draw_at"]
                if limit_row["quota_day"] != quota_day:
                    daily_results = 0
                    last_draw_at = None
                    await connection.execute(
                        "UPDATE draw_limits SET quota_day = ?, last_draw_at = NULL, daily_results = 0 WHERE visitor_id = ?",
                        (quota_day, visitor_id),
                    )
                required_cooldown = cooldown_for_daily_results(daily_results, cooldown_seconds)
                if required_cooldown and last_draw_at:
                    last_draw_datetime = datetime.fromisoformat(last_draw_at.replace("Z", "+00:00"))
                    elapsed = (now - last_draw_datetime).total_seconds()
                    if elapsed < required_cooldown:
                        retry_after = max(1, math.ceil(required_cooldown - elapsed))
                        raise DrawLimitError(
                            "抽取间隔太短，请稍后再试",
                            retry_after=retry_after,
                            details={
                                "reason": "cooldown",
                                "retry_after": retry_after,
                                "daily_results": daily_results,
                                "cooldown_seconds": required_cooldown,
                            },
                        )
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
                        special_type, special_asset, counts_as_cooking, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            encode_special_asset(result.special_asset_stems),
                            int(result.counts_as_cooking),
                            created_at,
                        )
                        for position, result in enumerate(results, 1)
                    ],
                )
                await connection.execute(
                    "UPDATE draw_limits SET last_draw_at = ?, quota_day = ?, daily_results = ? WHERE visitor_id = ?",
                    (created_at, quota_day, daily_results + result_count, visitor_id),
                )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        next_cooldown = cooldown_for_daily_results(daily_results + result_count, cooldown_seconds)
        return batch_id, created_at, next_cooldown

    async def profile(
        self,
        visitor_id: str,
        *,
        recent_limit: int = 50,
        recent_offset: int = 0,
        ranking_limit: int = 3,
        cooldown_seconds: int | None = None,
    ) -> dict:
        async with self.connect() as connection:
            rows = await (
                await connection.execute(
                    """
                    SELECT r.id, r.position, r.akito_name, r.toya_name, r.is_cooking,
                           r.special_type, r.special_asset, r.counts_as_cooking, r.created_at,
                           b.id AS batch_id, b.requested_count, b.fixed_side, b.fixed_name
                    FROM draw_results r
                    JOIN draw_batches b ON b.id = r.batch_id
                    WHERE b.visitor_id = ?
                    ORDER BY r.created_at DESC, b.rowid DESC, r.position DESC
                    """,
                    (visitor_id,),
                )
            ).fetchall()
            cooldown_remaining = None
            if cooldown_seconds is not None:
                limit_row = await (
                    await connection.execute(
                        "SELECT last_draw_at, quota_day, daily_results FROM draw_limits WHERE visitor_id = ?",
                        (visitor_id,),
                    )
                ).fetchone()
                cooldown_remaining = 0
                now = datetime.now(UTC)
                quota_day = now.astimezone(CHINA_TZ).date().isoformat()
                if limit_row and limit_row["quota_day"] == quota_day and limit_row["last_draw_at"]:
                    required_cooldown = cooldown_for_daily_results(
                        int(limit_row["daily_results"]), cooldown_seconds
                    )
                    last_draw_datetime = datetime.fromisoformat(
                        limit_row["last_draw_at"].replace("Z", "+00:00")
                    )
                    elapsed = (now - last_draw_datetime).total_seconds()
                    cooldown_remaining = max(0, math.ceil(required_cooldown - elapsed))

        draw_count = len(rows)
        random_draw_count = sum(1 for row in rows if row["fixed_side"] == "none")
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
        cooking_pairs = Counter(
            (row["akito_name"], row["toya_name"])
            for row in normal_rows
            if row["counts_as_cooking"] and row["akito_name"] and row["toya_name"]
        )
        # 「我最常被抽到做饭的组合」用 is_cooking：不含狐兔饭，跟上面含狐兔饭的
        # cooking_pairs 是两个口径。个人侧统计全部记录（固定模式也算），
        # 与既有的 pair_top 保持一致 —— 全站榜只算双方随机，两边有意区分开。
        cooking_pair_hits = Counter(
            (row["akito_name"], row["toya_name"])
            for row in normal_rows
            if row["is_cooking"] and row["akito_name"] and row["toya_name"]
        )
        rank_order: dict[tuple[str, str], int] = {}
        akito_order: dict[str, int] = {}
        toya_order: dict[str, int] = {}
        cooking_rank_order: dict[tuple[str, str], int] = {}
        for index, row in enumerate(rows):
            if row["special_type"]:
                continue
            akito_order.setdefault(row["akito_name"], index)
            toya_order.setdefault(row["toya_name"], index)
            rank_order.setdefault((row["akito_name"], row["toya_name"]), index)
            if row["is_cooking"]:
                cooking_rank_order.setdefault((row["akito_name"], row["toya_name"]), index)

        def top_names(counter: Counter, order: dict, limit: int = 3) -> list[dict]:
            values = sorted(counter.items(), key=lambda item: (-item[1], order.get(item[0], 10**9), str(item[0])))
            return [{"name": name, "count": count} for name, count in values[:limit]]

        pair_values = sorted(pair_hits.items(), key=lambda item: (-item[1], rank_order.get(item[0], 10**9), item[0]))
        cooking_pair_values = sorted(
            cooking_pair_hits.items(),
            key=lambda item: (-item[1], cooking_rank_order.get(item[0], 10**9), item[0]),
        )
        profile = {
            "draw_count": draw_count,
            "random_draw_count": random_draw_count,
            "cooking_count": cooking_count,
            "special_counts": dict(sorted(specials.items())),
            "akito_top": top_names(akito_hits, akito_order, ranking_limit),
            "toya_top": top_names(toya_hits, toya_order, ranking_limit),
            "pair_top": [
                {"akito_name": pair[0], "toya_name": pair[1], "count": count}
                for pair, count in pair_values[:ranking_limit]
            ],
            "cooking_pairs": [
                {"akito_name": pair[0], "toya_name": pair[1], "count": count}
                for pair, count in cooking_pairs.most_common()
            ],
            "cooking_pair_top": [
                {"akito_name": pair[0], "toya_name": pair[1], "count": count}
                for pair, count in cooking_pair_values[:ranking_limit]
            ],
            "recent": [
                _with_decoded_asset(row)
                for row in rows[recent_offset : recent_offset + recent_limit]
            ],
            "recent_total": len(rows),
            "recent_offset": recent_offset,
            "recent_limit": recent_limit,
            "recent_has_more": recent_offset + recent_limit < len(rows),
        }
        if cooldown_remaining is not None:
            profile["cooldown_remaining_seconds"] = cooldown_remaining
        return profile

    async def community_stats(self, *, limit: int = 10) -> dict:
        async with self.connect() as connection:
            total_row = await (
                await connection.execute("SELECT COUNT(*) AS total_draws FROM draw_results")
            ).fetchone()
            random_total_row = await (
                await connection.execute(
                    """
                    SELECT COUNT(*) AS random_draws
                    FROM draw_results r
                    JOIN draw_batches b ON b.id = r.batch_id
                    WHERE b.fixed_side = 'none'
                    """
                )
            ).fetchone()
            akito_rows = await (
                await connection.execute(
                    """
                    SELECT r.akito_name AS name, COUNT(*) AS count
                    FROM draw_results r
                    JOIN draw_batches b ON b.id = r.batch_id
                    WHERE b.fixed_side = 'none'
                      AND r.special_type IS NULL AND r.akito_name IS NOT NULL
                    GROUP BY r.akito_name
                    ORDER BY count DESC, name COLLATE NOCASE ASC
                    LIMIT ?
                    """,
                    (limit,),
                )
            ).fetchall()
            toya_rows = await (
                await connection.execute(
                    """
                    SELECT r.toya_name AS name, COUNT(*) AS count
                    FROM draw_results r
                    JOIN draw_batches b ON b.id = r.batch_id
                    WHERE b.fixed_side = 'none'
                      AND r.special_type IS NULL AND r.toya_name IS NOT NULL
                    GROUP BY r.toya_name
                    ORDER BY count DESC, name COLLATE NOCASE ASC
                    LIMIT ?
                    """,
                    (limit,),
                )
            ).fetchall()
            pair_rows = await (
                await connection.execute(
                    """
                    SELECT r.akito_name, r.toya_name, COUNT(*) AS count
                    FROM draw_results r
                    JOIN draw_batches b ON b.id = r.batch_id
                    WHERE b.fixed_side = 'none'
                      AND r.special_type IS NULL
                      AND r.akito_name IS NOT NULL
                      AND r.toya_name IS NOT NULL
                    GROUP BY r.akito_name, r.toya_name
                    ORDER BY count DESC, akito_name COLLATE NOCASE ASC, toya_name COLLATE NOCASE ASC
                    LIMIT ?
                    """,
                    (limit,),
                )
            ).fetchall()
            # 「最常被抽到做饭的组合」：口径是双方随机 + 普通做饭（is_cooking），
            # 不含狐兔饭 —— 那张卡跟「最常出现的组合」并排放，两张榜差就差在这个条件上
            cooking_pair_rows = await (
                await connection.execute(
                    """
                    SELECT r.akito_name, r.toya_name, COUNT(*) AS count
                    FROM draw_results r
                    JOIN draw_batches b ON b.id = r.batch_id
                    WHERE b.fixed_side = 'none'
                      AND r.is_cooking = 1
                      AND r.akito_name IS NOT NULL
                      AND r.toya_name IS NOT NULL
                    GROUP BY r.akito_name, r.toya_name
                    ORDER BY count DESC, r.akito_name COLLATE NOCASE ASC, r.toya_name COLLATE NOCASE ASC
                    LIMIT ?
                    """,
                    (limit,),
                )
            ).fetchall()
            # 单人纪录：只取最大值，不暴露是谁（访客本来就是匿名 cookie）
            cooking_record_row = await (
                await connection.execute(
                    """
                    SELECT MAX(hits) AS top FROM (
                        SELECT b.visitor_id AS visitor_id, COUNT(*) AS hits
                        FROM draw_results r
                        JOIN draw_batches b ON b.id = r.batch_id
                        WHERE r.counts_as_cooking = 1
                        GROUP BY b.visitor_id
                    )
                    """
                )
            ).fetchone()
            special_record_rows = await (
                await connection.execute(
                    """
                    SELECT special_type, MAX(hits) AS top FROM (
                        SELECT b.visitor_id AS visitor_id, r.special_type AS special_type, COUNT(*) AS hits
                        FROM draw_results r
                        JOIN draw_batches b ON b.id = r.batch_id
                        WHERE r.special_type IS NOT NULL
                        GROUP BY b.visitor_id, r.special_type
                    )
                    GROUP BY special_type
                    """
                )
            ).fetchall()

        return {
            "total_draws": int(total_row["total_draws"] if total_row else 0),
            "random_draws": int(random_total_row["random_draws"] if random_total_row else 0),
            "akito_top": [dict(row) for row in akito_rows],
            "toya_top": [dict(row) for row in toya_rows],
            "pair_top": [dict(row) for row in pair_rows],
            "cooking_pair_top": [dict(row) for row in cooking_pair_rows],
            # 单人纪录只给数字，不给身份
            "record_holders": {
                "cooking": int(cooking_record_row["top"] or 0) if cooking_record_row else 0,
                "specials": {
                    row["special_type"]: int(row["top"] or 0) for row in special_record_rows
                },
            },
        }

    async def clear_history(self, visitor_id: str) -> None:
        async with self.connect() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            try:
                await connection.execute("DELETE FROM draw_batches WHERE visitor_id = ?", (visitor_id,))
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
