from __future__ import annotations

from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone
import json
import math
from pathlib import Path
import time
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

-- MySekai 群友入口：QQ ↔ 游戏 ID 绑定，以及每次上传的审计记录。
-- 绑定资料只存公开信息（昵称/队长卡/头像），不存抓包内容。
CREATE TABLE IF NOT EXISTS mysekai_bindings (
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
CREATE INDEX IF NOT EXISTS idx_mysekai_bindings_game
    ON mysekai_bindings(server, game_user_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_mysekai_bindings_game
    ON mysekai_bindings(server, game_user_id);

CREATE TABLE IF NOT EXISTS mysekai_uploads (
    id TEXT PRIMARY KEY,
    qq TEXT NOT NULL,
    server TEXT NOT NULL,
    game_user_id TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    sites INTEGER NOT NULL,
    points INTEGER NOT NULL,
    drops INTEGER NOT NULL,
    push_status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mysekai_uploads_qq_created
    ON mysekai_uploads(qq, created_at DESC);

CREATE TABLE IF NOT EXISTS mysekai_visit_baselines (
    qq TEXT NOT NULL,
    day TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (qq, day)
);

CREATE TABLE IF NOT EXISTS mysekai_water_watches (
    qq TEXT PRIMARY KEY,
    enabled_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mysekai_water_rounds (
    game_user_id TEXT NOT NULL,
    round_key TEXT NOT NULL,
    pushed_at TEXT NOT NULL,
    matched INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (game_user_id, round_key)
);

CREATE TABLE IF NOT EXISTS mysekai_birthday_window (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    character_id INTEGER NOT NULL,
    character_name TEXT,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    observed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mysekai_highlight_materials (
    qq TEXT NOT NULL,
    resource_type TEXT NOT NULL DEFAULT 'mysekai_material',
    resource_id INTEGER NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (qq, resource_type, resource_id)
);

CREATE TABLE IF NOT EXISTS mysekai_convert_watches (
    qq TEXT PRIMARY KEY,
    enabled_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mysekai_convert_slots (
    game_user_id TEXT NOT NULL,
    slot_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    finish_at_ms INTEGER NOT NULL,
    outcome_count INTEGER NOT NULL DEFAULT 0,
    notified INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (game_user_id, slot_id)
);

CREATE TABLE IF NOT EXISTS mysekai_latest_capture (
    qq TEXT PRIMARY KEY,
    game_user_id TEXT NOT NULL,
    upload_id TEXT NOT NULL,
    captured_at_ms INTEGER,
    uploaded_at_ms INTEGER,
    map_png BLOB NOT NULL,
    card_png BLOB NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mysekai_push_windows (
    game_user_id TEXT NOT NULL,
    window_key TEXT NOT NULL,
    upload_id TEXT NOT NULL,
    pushed_at TEXT NOT NULL,
    PRIMARY KEY (game_user_id, window_key)
);

CREATE TABLE IF NOT EXISTS mysekai_catalog_state (
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
            state_columns = {
                row["name"]
                for row in (
                    await (
                        await connection.execute(
                            "PRAGMA table_info(mysekai_catalog_state)"
                        )
                    ).fetchall()
                )
            }
            for name, sql_type in (
                ("mysekai_rank", "INTEGER"),
                ("game_rank", "INTEGER"),
                ("refreshed_at_ms", "INTEGER"),
                ("fixtures", "TEXT"),
                ("convert_slots", "TEXT"),
                ("convert_histories", "TEXT"),
                ("suite_uploaded_at_ms", "INTEGER"),
                ("mysekai_uploaded_at_ms", "INTEGER"),
            ):
                if name not in state_columns:
                    await connection.execute(
                        f"ALTER TABLE mysekai_catalog_state ADD COLUMN {name} {sql_type}"
                    )
            capture_columns = {
                row["name"]
                for row in (
                    await (
                        await connection.execute(
                            "PRAGMA table_info(mysekai_latest_capture)"
                        )
                    ).fetchall()
                )
            }
            if "uploaded_at_ms" not in capture_columns:
                await connection.execute(
                    "ALTER TABLE mysekai_latest_capture ADD COLUMN uploaded_at_ms INTEGER"
                )
            round_columns = {
                row["name"]
                for row in (
                    await (
                        await connection.execute(
                            "PRAGMA table_info(mysekai_water_rounds)"
                        )
                    ).fetchall()
                )
            }
            if "matched" not in round_columns:
                await connection.execute(
                    "ALTER TABLE mysekai_water_rounds"
                    " ADD COLUMN matched INTEGER NOT NULL DEFAULT 0"
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

    async def create_mysekai_binding(
        self,
        *,
        qq: str,
        server: str,
        game_user_id: str,
        nickname: str | None,
        deck_id: int | None,
        leader_card_id: int | None,
        leader_avatar: str | None,
        profile_image_type: str | None,
    ) -> str | None:
        now = utc_now()
        async with self.connect() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            try:
                qq_row = await (
                    await connection.execute(
                        "SELECT 1 FROM mysekai_bindings WHERE qq = ?", (qq,)
                    )
                ).fetchone()
                if qq_row:
                    await connection.rollback()
                    return "qq"
                game_row = await (
                    await connection.execute(
                        "SELECT 1 FROM mysekai_bindings WHERE server = ? AND game_user_id = ?",
                        (server, game_user_id),
                    )
                ).fetchone()
                if game_row:
                    await connection.rollback()
                    return "game_user_id"
                await connection.execute(
                    """
                    INSERT INTO mysekai_bindings(
                        qq, server, game_user_id, nickname, deck_id,
                        leader_card_id, leader_avatar, profile_image_type,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        qq,
                        server,
                        game_user_id,
                        nickname,
                        deck_id,
                        leader_card_id,
                        leader_avatar,
                        profile_image_type,
                        now,
                        now,
                    ),
                )
                await connection.commit()
                return None
            except Exception:
                await connection.rollback()
                raise

    async def get_mysekai_binding(self, qq: str) -> dict | None:
        async with self.connect() as connection:
            row = await (
                await connection.execute(
                    "SELECT * FROM mysekai_bindings WHERE qq = ?", (qq,)
                )
            ).fetchone()
        return dict(row) if row else None

    async def get_mysekai_binding_by_game(
        self, server: str, game_user_id: str
    ) -> dict | None:
        async with self.connect() as connection:
            row = await (
                await connection.execute(
                    "SELECT * FROM mysekai_bindings WHERE server = ? AND game_user_id = ?",
                    (server, game_user_id),
                )
            ).fetchone()
        return dict(row) if row else None

    async def update_mysekai_binding_profile(
        self,
        *,
        qq: str,
        nickname: str | None,
        deck_id: int | None,
        leader_card_id: int | None,
        leader_avatar: str | None,
        profile_image_type: str | None,
    ) -> dict | None:
        async with self.connect() as connection:
            await connection.execute(
                """
                UPDATE mysekai_bindings
                SET nickname = ?, deck_id = ?, leader_card_id = ?,
                    leader_avatar = ?, profile_image_type = ?, updated_at = ?
                WHERE qq = ?
                """,
                (
                    nickname,
                    deck_id,
                    leader_card_id,
                    leader_avatar,
                    profile_image_type,
                    utc_now(),
                    qq,
                ),
            )
            row = await (
                await connection.execute(
                    "SELECT * FROM mysekai_bindings WHERE qq = ?", (qq,)
                )
            ).fetchone()
            await connection.commit()
        return dict(row) if row else None

    async def delete_mysekai_binding(self, qq: str) -> dict | None:
        async with self.connect() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            try:
                row = await (
                    await connection.execute(
                        "SELECT * FROM mysekai_bindings WHERE qq = ?", (qq,)
                    )
                ).fetchone()
                if row is None:
                    await connection.rollback()
                    return None
                await connection.execute(
                    "DELETE FROM mysekai_bindings WHERE qq = ?", (qq,)
                )
                await connection.commit()
                return dict(row)
            except Exception:
                await connection.rollback()
                raise

    async def get_mysekai_visit_baseline(self, qq: str, day: str) -> dict | None:
        """The visit counts captured on this QQ's first upload of ``day``."""
        async with self.connect() as connection:
            row = await (
                await connection.execute(
                    "SELECT payload FROM mysekai_visit_baselines WHERE qq = ? AND day = ?",
                    (qq, day),
                )
            ).fetchone()
        if row is None:
            return None
        try:
            data = json.loads(row["payload"])
        except (ValueError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    async def set_mysekai_visit_baseline(
        self, qq: str, day: str, payload: dict
    ) -> None:
        """Record the day's first baseline; later uploads never overwrite it."""
        async with self.connect() as connection:
            await connection.execute(
                """
                INSERT INTO mysekai_visit_baselines(qq, day, payload, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(qq, day) DO NOTHING
                """,
                (qq, day, json.dumps(payload, ensure_ascii=False), utc_now()),
            )
            await connection.commit()

    async def set_mysekai_water_watch(self, qq: str, enabled: bool) -> None:
        """Turn the watering watch on or off for one QQ.

        Enabling an already-enabled watch is a no-op: ``enabled_at`` is the
        counting start, and a repeated command must not reset it. To start a
        fresh count, close first and then open again.
        """
        async with self.connect() as connection:
            if enabled:
                await connection.execute(
                    """
                    INSERT INTO mysekai_water_watches(qq, enabled_at)
                    VALUES (?, ?)
                    ON CONFLICT(qq) DO NOTHING
                    """,
                    (qq, utc_now()),
                )
            else:
                await connection.execute(
                    "DELETE FROM mysekai_water_watches WHERE qq = ?", (qq,)
                )
            await connection.commit()

    async def is_mysekai_water_watch(self, qq: str) -> bool:
        async with self.connect() as connection:
            row = await (
                await connection.execute(
                    "SELECT 1 FROM mysekai_water_watches WHERE qq = ?", (qq,)
                )
            ).fetchone()
        return row is not None

    async def mysekai_water_watch_enabled_at(self, qq: str) -> str | None:
        """When this QQ last opened the watering watch."""
        async with self.connect() as connection:
            row = await (
                await connection.execute(
                    "SELECT enabled_at FROM mysekai_water_watches WHERE qq = ?", (qq,)
                )
            ).fetchone()
        return str(row[0]) if row is not None else None

    async def set_mysekai_convert_watch(self, qq: str, enabled: bool) -> None:
        """Turn the converter-completion watch on or off for one QQ."""
        async with self.connect() as connection:
            if enabled:
                await connection.execute(
                    """
                    INSERT INTO mysekai_convert_watches(qq, enabled_at)
                    VALUES (?, ?)
                    ON CONFLICT(qq) DO UPDATE SET enabled_at = excluded.enabled_at
                    """,
                    (qq, utc_now()),
                )
                # 订阅这一刻之前就已完成（甚至已领取）的槽不再补推，
                # 否则刚订阅就被历史槽刷屏。
                await connection.execute(
                    """
                    UPDATE mysekai_convert_slots
                       SET notified = 1
                     WHERE finish_at_ms <= ?
                       AND game_user_id IN (
                             SELECT game_user_id FROM mysekai_bindings WHERE qq = ?
                       )
                    """,
                    (int(time.time() * 1000), qq),
                )
            else:
                await connection.execute(
                    "DELETE FROM mysekai_convert_watches WHERE qq = ?", (qq,)
                )
            await connection.commit()

    async def clear_mysekai_water_watches(self) -> int:
        """Turn the watering watch off for every QQ.

        Used by the superuser command after a logic change that old
        subscriptions would misrepresent; players opt in again themselves.
        Round history is kept, but counting restarts from each player's
        next ``开启浇水监听``.
        """
        async with self.connect() as connection:
            row = await (
                await connection.execute("SELECT COUNT(*) FROM mysekai_water_watches")
            ).fetchone()
            await connection.execute("DELETE FROM mysekai_water_watches")
            await connection.commit()
        return int(row[0]) if row else 0

    async def set_mysekai_birthday_window(
        self,
        *,
        character_id: int,
        character_name: str | None,
        start_ms: int,
        end_ms: int,
    ) -> None:
        """Remember the birthday window currently being observed."""
        async with self.connect() as connection:
            await connection.execute(
                """
                INSERT INTO mysekai_birthday_window(
                    id, character_id, character_name, start_ms, end_ms, observed_at
                )
                VALUES (1, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    character_id = excluded.character_id,
                    character_name = excluded.character_name,
                    start_ms = excluded.start_ms,
                    end_ms = excluded.end_ms,
                    observed_at = excluded.observed_at
                """,
                (character_id, character_name, start_ms, end_ms, utc_now()),
            )
            await connection.commit()

    async def get_mysekai_birthday_window(self) -> dict | None:
        """The last birthday window this service observed, if any."""
        async with self.connect() as connection:
            row = await (
                await connection.execute(
                    """
                    SELECT character_id, character_name, start_ms, end_ms, observed_at
                      FROM mysekai_birthday_window
                     WHERE id = 1
                    """
                )
            ).fetchone()
        return dict(row) if row is not None else None

    async def delete_mysekai_birthday_window(self) -> None:
        """Forget the observed window once its watches have been closed."""
        async with self.connect() as connection:
            await connection.execute(
                "DELETE FROM mysekai_birthday_window WHERE id = 1"
            )
            await connection.commit()

    async def list_mysekai_highlight_materials(self, qq: str) -> list[int]:
        """Material ids this QQ asked to highlight, in insertion order."""
        async with self.connect() as connection:
            rows = await (
                await connection.execute(
                    """
                    SELECT resource_id
                      FROM mysekai_highlight_materials
                     WHERE qq = ? AND resource_type = 'mysekai_material'
                     ORDER BY added_at, resource_id
                    """,
                    (qq,),
                )
            ).fetchall()
        return [int(row[0]) for row in rows]

    async def set_mysekai_highlight_materials(
        self, qq: str, resource_ids: list[int]
    ) -> None:
        """Replace this QQ's whole highlight list in one transaction."""
        async with self.connect() as connection:
            await connection.execute(
                "DELETE FROM mysekai_highlight_materials WHERE qq = ?", (qq,)
            )
            now = utc_now()
            await connection.executemany(
                """
                INSERT INTO mysekai_highlight_materials(
                    qq, resource_type, resource_id, added_at
                ) VALUES (?, 'mysekai_material', ?, ?)
                """,
                [(qq, int(resource_id), now) for resource_id in resource_ids],
            )
            await connection.commit()

    async def remove_mysekai_highlight_materials(
        self, qq: str, resource_ids: list[int]
    ) -> int:
        """Remove the named material ids; returns how many rows were deleted."""
        if not resource_ids:
            return 0
        placeholders = ",".join("?" for _ in resource_ids)
        async with self.connect() as connection:
            cursor = await connection.execute(
                f"""
                DELETE FROM mysekai_highlight_materials
                 WHERE qq = ?
                   AND resource_type = 'mysekai_material'
                   AND resource_id IN ({placeholders})
                """,
                (qq, *resource_ids),
            )
            await connection.commit()
        return int(cursor.rowcount or 0)

    async def clear_mysekai_highlight_materials(self, qq: str) -> int:
        """Drop every extra highlight for one QQ; returns the old count."""
        async with self.connect() as connection:
            row = await (
                await connection.execute(
                    "SELECT COUNT(*) FROM mysekai_highlight_materials WHERE qq = ?",
                    (qq,),
                )
            ).fetchone()
            await connection.execute(
                "DELETE FROM mysekai_highlight_materials WHERE qq = ?", (qq,)
            )
            await connection.commit()
        return int(row[0]) if row else 0

    async def clear_mysekai_convert_watches(self) -> tuple[int, int]:
        """Drop every converter subscription together with its slot rows.

        Used by the superuser command after a change the old rows would
        misrepresent: players subscribe again, and subscribing re-reads their
        latest capture.
        """
        async with self.connect() as connection:
            watched = await (
                await connection.execute("SELECT COUNT(*) FROM mysekai_convert_watches")
            ).fetchone()
            slotted = await (
                await connection.execute("SELECT COUNT(*) FROM mysekai_convert_slots")
            ).fetchone()
            await connection.execute("DELETE FROM mysekai_convert_watches")
            await connection.execute("DELETE FROM mysekai_convert_slots")
            await connection.commit()
        return (int(watched[0]) if watched else 0, int(slotted[0]) if slotted else 0)

    async def save_mysekai_latest_capture(
        self,
        *,
        qq: str,
        game_user_id: str,
        upload_id: str,
        captured_at_ms: int | None,
        uploaded_at_ms: int | None,
        map_png: bytes,
        card_png: bytes,
    ) -> None:
        """Keep the newest upload's two images so the bot can show them on demand."""
        async with self.connect() as connection:
            await connection.execute(
                """
                INSERT INTO mysekai_latest_capture(
                    qq, game_user_id, upload_id, captured_at_ms, uploaded_at_ms,
                    map_png, card_png, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(qq) DO UPDATE SET
                    game_user_id = excluded.game_user_id,
                    upload_id = excluded.upload_id,
                    captured_at_ms = excluded.captured_at_ms,
                    uploaded_at_ms = excluded.uploaded_at_ms,
                    map_png = excluded.map_png,
                    card_png = excluded.card_png,
                    updated_at = excluded.updated_at
                """,
                (
                    qq,
                    game_user_id,
                    upload_id,
                    captured_at_ms,
                    uploaded_at_ms,
                    map_png,
                    card_png,
                    utc_now(),
                ),
            )
            await connection.commit()

    async def get_mysekai_latest_capture(self, qq: str) -> dict | None:
        async with self.connect() as connection:
            row = await (
                await connection.execute(
                    """
                    SELECT captured_at_ms, uploaded_at_ms, map_png, card_png
                      FROM mysekai_latest_capture
                     WHERE qq = ?
                    """,
                    (qq,),
                )
            ).fetchone()
        if row is None:
            return None
        return {
            "captured_at_ms": row[0],
            "uploaded_at_ms": row[1],
            "map_png": bytes(row[2]),
            "card_png": bytes(row[3]),
        }

    async def set_mysekai_data_times(
        self,
        *,
        server: str,
        game_user_id: str,
        suite_uploaded_at_ms: int | None = None,
        mysekai_uploaded_at_ms: int | None = None,
    ) -> None:
        """Record when each kind of data last reached us.

        ``suite`` is Haruki's own upload time for the published suite;
        ``mysekai`` is when the capture carrying the MySekai data arrived.
        Either may be omitted, and omitting one keeps the stored value.
        """
        async with self.connect() as connection:
            await connection.execute(
                """
                INSERT INTO mysekai_catalog_state(
                    server, game_user_id, suite_uploaded_at_ms,
                    mysekai_uploaded_at_ms, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(server, game_user_id) DO UPDATE SET
                    suite_uploaded_at_ms=COALESCE(
                        excluded.suite_uploaded_at_ms, suite_uploaded_at_ms
                    ),
                    mysekai_uploaded_at_ms=COALESCE(
                        excluded.mysekai_uploaded_at_ms, mysekai_uploaded_at_ms
                    ),
                    updated_at=excluded.updated_at
                """,
                (
                    server,
                    game_user_id,
                    suite_uploaded_at_ms,
                    mysekai_uploaded_at_ms,
                    utc_now(),
                ),
            )
            await connection.commit()

    async def claim_mysekai_push_window(
        self, game_user_id: str, window_key: str, upload_id: str
    ) -> bool:
        """Take this player's one push slot for the refresh window.

        Returns ``True`` only for the first upload in the window, so the
        caller pushes then and stays silent for the rest of it. A failed
        push releases the slot again (see ``release_mysekai_push_window``).
        """
        async with self.connect() as connection:
            cursor = await connection.execute(
                """
                INSERT OR IGNORE INTO mysekai_push_windows(
                    game_user_id, window_key, upload_id, pushed_at
                ) VALUES (?, ?, ?, ?)
                """,
                (game_user_id, window_key, upload_id, utc_now()),
            )
            await connection.commit()
            claimed = cursor.rowcount == 1
            await cursor.close()
        return claimed

    async def release_mysekai_push_window(
        self, game_user_id: str, window_key: str, upload_id: str
    ) -> None:
        """Give the window back after a failed push so a retry can still send."""
        async with self.connect() as connection:
            await connection.execute(
                """
                DELETE FROM mysekai_push_windows
                 WHERE game_user_id = ? AND window_key = ? AND upload_id = ?
                """,
                (game_user_id, window_key, upload_id),
            )
            await connection.commit()

    async def is_mysekai_convert_watch(self, qq: str) -> bool:
        async with self.connect() as connection:
            row = await (
                await connection.execute(
                    "SELECT 1 FROM mysekai_convert_watches WHERE qq = ?", (qq,)
                )
            ).fetchone()
        return row is not None

    async def record_mysekai_convert_slots(
        self, game_user_id: str, slots: list, *, quiet_past: bool = False
    ) -> None:
        """Store each slot's finish time; only a new batch resets its notification.

        A batch is "new" when its finish time moves **later** than the stored
        one. Finishing earlier means the same run was sped up (a shortcut item
        or a shorter per-item time), not a new batch, so an already-sent
        notice stays sent. Every notified row keeps a past finish time — it
        only becomes notified once it has finished — while a new batch starts
        now and therefore always ends later, which is what makes the
        comparison safe.

        ``quiet_past`` marks already-finished slots as notified at insert time.
        Subscribing uses it: a player who uploaded a capture before opting in
        can be reminded about the runs still going, but should not be told
        about batches that ended before they subscribed.
        """
        now_ms = int(time.time() * 1000)
        async with self.connect() as connection:
            for slot in slots:
                try:
                    finish_at = (
                        int(slot["convertStartAt"])
                        + int(slot["convertCount"])
                        * int(slot["onceConvertMinutes"])
                        * 60_000
                    )
                    item_id = int(slot["mysekaiConvertItemId"])
                    slot_id = int(slot["slotId"])
                    outcome_count = len(
                        slot.get("userMysekaiConvertObtainResources") or []
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                notified = 1 if quiet_past and finish_at <= now_ms else 0
                await connection.execute(
                    """
                    INSERT INTO mysekai_convert_slots(
                        game_user_id, slot_id, item_id, finish_at_ms,
                        outcome_count, notified, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(game_user_id, slot_id) DO UPDATE SET
                        notified = CASE
                            WHEN excluded.notified = 1 THEN 1
                            WHEN excluded.finish_at_ms
                                 > mysekai_convert_slots.finish_at_ms THEN 0
                            ELSE mysekai_convert_slots.notified
                        END,
                        item_id = excluded.item_id,
                        finish_at_ms = excluded.finish_at_ms,
                        outcome_count = excluded.outcome_count,
                        updated_at = excluded.updated_at
                    """,
                    (
                        game_user_id,
                        slot_id,
                        item_id,
                        finish_at,
                        outcome_count,
                        notified,
                        utc_now(),
                    ),
                )
            await connection.commit()

    async def due_mysekai_convert_slots(self, now_ms: int) -> list[dict]:
        """Finished slots of subscribed players, oldest finish first."""
        async with self.connect() as connection:
            cursor = await connection.execute(
                """
                SELECT s.game_user_id, s.slot_id, s.item_id, s.finish_at_ms,
                       s.outcome_count, b.qq
                  FROM mysekai_convert_slots s
                  JOIN mysekai_bindings b
                    ON b.game_user_id = s.game_user_id AND b.server = 'cn'
                  JOIN mysekai_convert_watches w ON w.qq = b.qq
                 WHERE s.notified = 0 AND s.finish_at_ms <= ?
                 ORDER BY s.finish_at_ms
                """,
                (now_ms,),
            )
            rows = await cursor.fetchall()
        return [
            {
                "game_user_id": row[0],
                "slot_id": row[1],
                "item_id": row[2],
                "finish_at_ms": row[3],
                "outcome_count": row[4],
                "qq": row[5],
            }
            for row in rows
        ]

    async def ack_mysekai_convert_slots(self, game_user_id: str, slot_ids: list[int]) -> None:
        if not slot_ids:
            return
        placeholders = ",".join("?" for _ in slot_ids)
        async with self.connect() as connection:
            await connection.execute(
                f"""
                UPDATE mysekai_convert_slots
                   SET notified = 1
                 WHERE game_user_id = ? AND slot_id IN ({placeholders})
                """,
                (game_user_id, *slot_ids),
            )
            await connection.commit()

    async def release_mysekai_water_round(
        self, game_user_id: str, round_key: str
    ) -> None:
        """Release a failed push; only the successful claimant may call this."""
        async with self.connect() as connection:
            await connection.execute(
                "DELETE FROM mysekai_water_rounds WHERE game_user_id = ? AND round_key = ?",
                (game_user_id, round_key),
            )
            await connection.commit()

    async def claim_mysekai_water_round(
        self, game_user_id: str, round_key: str
    ) -> bool:
        """Claim an unseen round without moving a party's progress backwards."""
        async with self.connect() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            parts = round_key.split(":")
            if len(parts) == 4 and parts[0] == "party" and parts[2] == "progress" and parts[3].isdigit():
                prefix = ":".join(parts[:3]) + ":"
                async with connection.execute(
                    "SELECT round_key FROM mysekai_water_rounds WHERE game_user_id = ? AND round_key LIKE ?",
                    (game_user_id, prefix + "%"),
                ) as previous:
                    rows = await previous.fetchall()
                if any(
                    row[0][len(prefix):].isdigit()
                    and int(row[0][len(prefix):]) >= int(parts[3])
                    for row in rows
                ):
                    return False
            cursor = await connection.execute(
                """
                INSERT OR IGNORE INTO mysekai_water_rounds(
                    game_user_id, round_key, pushed_at
                ) VALUES (?, ?, ?)
                """,
                (game_user_id, round_key, utc_now()),
            )
            await connection.commit()
        return cursor.rowcount > 0

    async def mark_mysekai_water_round_matched(
        self, game_user_id: str, round_key: str
    ) -> None:
        """Remember that this round carried a watched drop."""
        async with self.connect() as connection:
            await connection.execute(
                """
                UPDATE mysekai_water_rounds SET matched = 1
                 WHERE game_user_id = ? AND round_key = ?
                """,
                (game_user_id, round_key),
            )
            await connection.commit()

    async def mysekai_water_round_stats(
        self, game_user_id: str, *, since: str | None = None
    ) -> dict:
        """How many rounds were observed for this account, and how many hit.

        ``since`` is the watch-enable timestamp, so the count answers
        "since I opened the listener", not "since this account ever played".
        """
        clause = " AND pushed_at >= ?" if since else ""
        params = (game_user_id, since) if since else (game_user_id,)
        async with self.connect() as connection:
            async with connection.execute(
                f"""
                SELECT COUNT(*),
                       COALESCE(SUM(matched), 0),
                       MAX(pushed_at)
                  FROM mysekai_water_rounds
                 WHERE game_user_id = ?{clause}
                """,
                params,
            ) as cursor:
                total, matched, latest = await cursor.fetchone()
        return {
            "rounds": int(total or 0),
            "matched_rounds": int(matched or 0),
            "latest_round_at": latest,
        }

    async def set_mysekai_catalog_state(
        self,
        *,
        server: str,
        game_user_id: str,
        music_records: list | None,
        blueprints: list | None,
        fixtures: list | None = None,
        convert_slots: list | None = None,
        convert_histories: list | None = None,
        mysekai_rank: int | None = None,
        refreshed_at_ms: int | None = None,
    ) -> None:
        """Keep the id rows from the account's most recent capture.

        ``None`` and ``[]`` mean different things here: ``None`` is "this capture
        did not refresh the resource", ``[]`` is "refreshed, and the player owns
        none". The renderer greys tiles only in the second case.
        """
        async with self.connect() as connection:
            await connection.execute(
                """
                INSERT INTO mysekai_catalog_state(
                    server, game_user_id, music_records, blueprints, fixtures,
                    convert_slots, convert_histories,
                    mysekai_rank, refreshed_at_ms, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(server, game_user_id) DO UPDATE SET
                    -- 抓包只带变化的子表；NULL 表示「这次没刷新」，必须保留旧值。
                    -- 空列表是「刷新了、一个都没有」，会正常覆盖。
                    music_records=COALESCE(excluded.music_records, music_records),
                    blueprints=COALESCE(excluded.blueprints, blueprints),
                    fixtures=COALESCE(excluded.fixtures, fixtures),
                    convert_slots=COALESCE(excluded.convert_slots, convert_slots),
                    convert_histories=COALESCE(
                        excluded.convert_histories, convert_histories
                    ),
                    mysekai_rank=COALESCE(excluded.mysekai_rank, mysekai_rank),
                    refreshed_at_ms=COALESCE(
                        excluded.refreshed_at_ms, refreshed_at_ms
                    ),
                    updated_at=excluded.updated_at
                """,
                (
                    server,
                    game_user_id,
                    None if music_records is None else json.dumps(music_records),
                    None if blueprints is None else json.dumps(blueprints),
                    None if fixtures is None else json.dumps(fixtures),
                    None if convert_slots is None else json.dumps(convert_slots),
                    None if convert_histories is None else json.dumps(convert_histories),
                    mysekai_rank,
                    refreshed_at_ms,
                    utc_now(),
                ),
            )
            await connection.commit()

    async def get_mysekai_catalog_state(
        self, server: str, game_user_id: str
    ) -> dict | None:
        async with self.connect() as connection:
            cursor = await connection.execute(
                """
                SELECT music_records, blueprints, fixtures,
                       convert_slots, convert_histories,
                       mysekai_rank, refreshed_at_ms, updated_at,
                       suite_uploaded_at_ms, mysekai_uploaded_at_ms, game_rank
                FROM mysekai_catalog_state
                WHERE server = ? AND game_user_id = ?
                """,
                (server, game_user_id),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "music_records": None if row[0] is None else json.loads(row[0]),
            "blueprints": None if row[1] is None else json.loads(row[1]),
            "fixtures": None if row[2] is None else json.loads(row[2]),
            "convert_slots": None if row[3] is None else json.loads(row[3]),
            "convert_histories": None if row[4] is None else json.loads(row[4]),
            "mysekai_rank": row[5],
            "refreshed_at_ms": row[6],
            "updated_at": row[7],
            "suite_uploaded_at_ms": row[8],
            "mysekai_uploaded_at_ms": row[9],
            "game_rank": row[10],
        }

    async def set_mysekai_game_rank(
        self, server: str, game_user_id: str, rank: int
    ) -> None:
        """Cache the account's game level.

        The capture carries only the 烤森 level, so this is the one value that
        has to come from Haruki. Cached here so the cards do not pay an extra
        request on every command.
        """
        async with self.connect() as connection:
            await connection.execute(
                """
                INSERT INTO mysekai_catalog_state(
                    server, game_user_id, game_rank, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(server, game_user_id) DO UPDATE SET
                    game_rank = excluded.game_rank
                """,
                (server, game_user_id, rank, utc_now()),
            )
            await connection.commit()

    async def record_mysekai_upload(
        self,
        *,
        upload_id: str,
        qq: str,
        server: str,
        game_user_id: str,
        body_sha256: str,
        sites: int,
        points: int,
        drops: int,
        push_status: str,
    ) -> None:
        async with self.connect() as connection:
            await connection.execute(
                """
                INSERT INTO mysekai_uploads(
                    id, qq, server, game_user_id, body_sha256,
                    sites, points, drops, push_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    upload_id,
                    qq,
                    server,
                    game_user_id,
                    body_sha256,
                    sites,
                    points,
                    drops,
                    push_status,
                    utc_now(),
                ),
            )
            await connection.commit()

    async def update_mysekai_upload_push_status(
        self, upload_id: str, push_status: str
    ) -> None:
        async with self.connect() as connection:
            await connection.execute(
                "UPDATE mysekai_uploads SET push_status = ? WHERE id = ?",
                (push_status, upload_id),
            )
            await connection.commit()

    async def clear_history(self, visitor_id: str) -> None:
        async with self.connect() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            try:
                await connection.execute("DELETE FROM draw_batches WHERE visitor_id = ?", (visitor_id,))
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
