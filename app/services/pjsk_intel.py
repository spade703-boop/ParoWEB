"""pjsk-intel 持久化与发布队列（工作计划 §12）。

五张表（sources / runs / reports / entities / deliveries）建在 ParoWEB 现有
SQLite 里。投递用租约机制：进程在发送中崩溃后，过期租约可被新进程接管
（§12.5）。图片只落盘，数据库存路径、哈希、大小和过期时间（§11.3），不
保存 Base64。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time
from pathlib import Path

import aiosqlite
import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS pjsk_intel_sources (
    source TEXT PRIMARY KEY,
    published_sha TEXT,
    last_detected_sha TEXT,
    last_success_at INTEGER,
    last_error TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pjsk_intel_runs (
    run_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    from_sha TEXT,
    to_sha TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    error TEXT
);
CREATE TABLE IF NOT EXISTS pjsk_intel_reports (
    report_id TEXT PRIMARY KEY,
    run_id TEXT,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    counts TEXT NOT NULL DEFAULT '{}',
    public_json TEXT NOT NULL,
    image_path TEXT,
    image_sha256 TEXT,
    image_bytes INTEGER NOT NULL DEFAULT 0,
    image_expires_at INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS pjsk_intel_reports_created
    ON pjsk_intel_reports(created_at DESC);
CREATE TABLE IF NOT EXISTS pjsk_intel_entities (
    report_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    change_type TEXT NOT NULL,
    title TEXT NOT NULL,
    starts_at INTEGER NOT NULL DEFAULT 0,
    ends_at INTEGER NOT NULL DEFAULT 0,
    payload TEXT NOT NULL,
    PRIMARY KEY (report_id, entity_type, entity_id)
);
CREATE TABLE IF NOT EXISTS pjsk_intel_deliveries (
    report_id TEXT NOT NULL,
    target TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'sending', 'delivered', 'retry', 'permanent_failed')),
    lease_until INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_retry_at INTEGER NOT NULL DEFAULT 0,
    message_id TEXT,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (report_id, target)
);
"""

#: 投递重试退避（分钟）：1、5、15、60，之后每 60 分钟一次。
RETRY_BACKOFF_SECONDS = (60, 300, 900, 3600)
RETRY_CEILING_SECONDS = 3600
MAX_ATTEMPTS = 12
#: ``sending`` 租约超过该秒数视为进程崩溃残留，可被接管（§17.2）。
LEASE_SECONDS = 300


class PjskIntelStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database_path = settings.database_path
        self.image_dir = settings.pjsk_intel_image_dir

    async def initialize(self) -> None:
        Path(self.image_dir).mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.executescript(SCHEMA)
            await connection.commit()

    def _connect(self) -> aiosqlite.Connection:
        return aiosqlite.connect(self.database_path)

    # -- 入库（§13.1）----------------------------------------------------------
    async def ingest(self, payload: dict) -> dict:
        """幂等入库；已存在的 report_id 直接返回 duplicate。"""
        report = payload["report"]
        report_id = payload["report_id"]
        async with self._connect() as connection:
            cursor = await connection.execute(
                "SELECT report_id FROM pjsk_intel_reports WHERE report_id = ?",
                (report_id,),
            )
            if await cursor.fetchone():
                return {"ok": True, "duplicate": True, "report_id": report_id}
            image_path = None
            image_sha256 = None
            image_bytes = 0
            image_expires_at = 0
            image = base64.b64decode(payload["image_png_base64"], validate=True)
            if image and not image.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("image is not a png")
            if len(image) > 9 * 1024 * 1024:
                raise ValueError("image too large")
            if image:
                image_sha256 = hashlib.sha256(image).hexdigest()
                image_bytes = len(image)
                image_path = str(Path(self.image_dir) / ("%s.png" % report_id))
                Path(image_path).write_bytes(image)
                image_expires_at = int(
                    time.time() + self.settings.pjsk_intel_image_ttl_days * 86400
                )
            now = int(time.time())
            await connection.execute(
                "INSERT INTO pjsk_intel_reports"
                "(report_id, run_id, source, title, summary, counts, public_json,"
                " image_path, image_sha256, image_bytes, image_expires_at, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    report_id,
                    payload.get("run_id"),
                    str(report.get("source") or ""),
                    str(report.get("title") or "国服更新"),
                    str(report.get("summary") or ""),
                    json.dumps(
                        _counts(report), ensure_ascii=False, separators=(",", ":")
                    ),
                    json.dumps(report, ensure_ascii=False, sort_keys=True),
                    image_path,
                    image_sha256,
                    image_bytes,
                    image_expires_at,
                    now,
                ),
            )
            for obj in list(report.get("objects") or []) + list(report.get("corrections") or []):
                await connection.execute(
                    "INSERT OR IGNORE INTO pjsk_intel_entities"
                    "(report_id, entity_type, entity_id, change_type, title,"
                    " starts_at, ends_at, payload)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        report_id,
                        str(obj.get("entity_type") or ""),
                        str(obj.get("entity_id") or ""),
                        str(obj.get("change_type") or ""),
                        str(obj.get("title") or ""),
                        int(obj.get("starts_at") or 0),
                        int(obj.get("ends_at") or 0),
                        json.dumps(obj, ensure_ascii=False, sort_keys=True),
                    ),
                )
            await connection.execute(
                "INSERT INTO pjsk_intel_deliveries"
                "(report_id, target, status, updated_at) VALUES (?, ?, 'pending', ?)",
                (report_id, self.settings.pjsk_intel_target_group, now),
            )
            await connection.execute(
                "INSERT INTO pjsk_intel_sources(source, published_sha, last_success_at)"
                " VALUES (?, ?, ?)"
                " ON CONFLICT(source) DO UPDATE SET"
                " published_sha = excluded.published_sha,"
                " last_success_at = excluded.last_success_at,"
                " last_error = NULL, consecutive_failures = 0",
                (
                    str(report.get("source") or ""),
                    str(report.get("to_sha") or ""),
                    now,
                ),
            )
            await connection.commit()
        return {"ok": True, "duplicate": False, "report_id": report_id}

    # -- 运行状态（§13.1 run-status）--------------------------------------------
    async def record_run(
        self, source: str, run_id: str, from_sha: str | None, to_sha: str, status: str, error: str | None
    ) -> None:
        async with self._connect() as connection:
            await connection.execute(
                "INSERT INTO pjsk_intel_runs"
                "(run_id, source, from_sha, to_sha, status, updated_at, error)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(run_id) DO UPDATE SET"
                " status = excluded.status, updated_at = excluded.updated_at,"
                " error = excluded.error",
                (run_id, source, from_sha, to_sha, status, int(time.time()), error),
            )
            if status == "published":
                await connection.execute(
                    "INSERT INTO pjsk_intel_sources"
                    "(source, published_sha, last_success_at)"
                    " VALUES (?, ?, ?)"
                    " ON CONFLICT(source) DO UPDATE SET"
                    " published_sha = excluded.published_sha,"
                    " last_success_at = excluded.last_success_at",
                    (source, to_sha, int(time.time())),
                )
            elif status.endswith("_failed") or status == "publish_retry":
                await connection.execute(
                    "INSERT INTO pjsk_intel_sources(source, last_error,"
                    " consecutive_failures) VALUES (?, ?, 1)"
                    " ON CONFLICT(source) DO UPDATE SET"
                    " last_error = excluded.last_error,"
                    " consecutive_failures = consecutive_failures + 1",
                    (source, error),
                )
            await connection.commit()

    # -- 查询（§13.3）-----------------------------------------------------------
    async def latest_report(self) -> dict | None:
        async with self._connect() as connection:
            cursor = await connection.execute(
                "SELECT r.*, d.status AS delivery_status, d.message_id"
                " FROM pjsk_intel_reports r"
                " LEFT JOIN pjsk_intel_deliveries d ON d.report_id = r.report_id"
                " ORDER BY r.created_at DESC LIMIT 1"
            )
            row = await cursor.fetchone()
            return _report_row(row)

    async def history(self, limit: int = 10) -> list[dict]:
        async with self._connect() as connection:
            cursor = await connection.execute(
                "SELECT r.report_id, r.title, r.summary, r.counts, r.created_at,"
                " d.status AS delivery_status, d.message_id"
                " FROM pjsk_intel_reports r"
                " LEFT JOIN pjsk_intel_deliveries d ON d.report_id = r.report_id"
                " ORDER BY r.created_at DESC LIMIT ?",
                (max(1, min(50, limit)),),
            )
            rows = await cursor.fetchall()
            return [_history_row(row) for row in rows]

    async def source_state(self, source: str | None = None) -> dict:
        async with self._connect() as connection:
            connection.row_factory = aiosqlite.Row
            if source:
                cursor = await connection.execute(
                    "SELECT * FROM pjsk_intel_sources WHERE source = ?", (source,)
                )
            else:
                cursor = await connection.execute(
                    "SELECT * FROM pjsk_intel_sources ORDER BY source"
                )
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
            row = await cursor.fetchone()
            return dict(row) if row else {}

    async def health(self) -> dict:
        async with self._connect() as connection:
            cursor = await connection.execute(
                "SELECT status, COUNT(*) AS n FROM pjsk_intel_deliveries GROUP BY status"
            )
            queue = {row[0]: row[1] for row in await cursor.fetchall()}
            cursor = await connection.execute(
                "SELECT COUNT(*) FROM pjsk_intel_reports"
            )
            (reports,) = await cursor.fetchone()
        return {"ok": True, "queue": queue, "reports": reports}

    async def report_image(self, report_id: str) -> Path | None:
        async with self._connect() as connection:
            cursor = await connection.execute(
                "SELECT image_path, image_expires_at FROM pjsk_intel_reports"
                " WHERE report_id = ?",
                (report_id,),
            )
            row = await cursor.fetchone()
        if not row or not row[0]:
            return None
        path = Path(row[0])
        if row[1] and int(row[1]) < time.time():
            return None
        return path if path.is_file() else None

    # -- 发布队列（§12.5、§14.1）------------------------------------------------
    async def claim_delivery(self) -> tuple[str, str, str] | None:
        """租约领取一条到期待投递报告：(report_id, target, summary)。"""
        now = int(time.time())
        async with self._connect() as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                "SELECT d.report_id, d.target FROM pjsk_intel_deliveries d"
                " WHERE (d.status IN ('pending', 'retry') AND d.next_retry_at <= ?)"
                " OR (d.status = 'sending' AND d.lease_until < ?)"
                " ORDER BY d.updated_at LIMIT 1",
                (now, now),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            await connection.execute(
                "UPDATE pjsk_intel_deliveries SET status = 'sending',"
                " lease_until = ?, updated_at = ? WHERE report_id = ? AND target = ?",
                (now + LEASE_SECONDS, now, row["report_id"], row["target"]),
            )
            await connection.commit()
        async with self._connect() as connection:
            cursor = await connection.execute(
                "SELECT summary, image_path, image_expires_at FROM pjsk_intel_reports"
                " WHERE report_id = ?",
                (row["report_id"],),
            )
            report = await cursor.fetchone()
        summary = report[0] if report else ""
        return row["report_id"], row["target"], summary

    async def finish_delivery(self, report_id: str, target: str, message_id: str) -> None:
        async with self._connect() as connection:
            await connection.execute(
                "UPDATE pjsk_intel_deliveries SET status = 'delivered',"
                " message_id = ?, updated_at = ?, lease_until = 0 WHERE"
                " report_id = ? AND target = ?",
                (message_id, int(time.time()), report_id, target),
            )
            await connection.commit()

    async def retry_delivery(self, report_id: str, target: str, error: str | None) -> None:
        async with self._connect() as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                "SELECT attempts FROM pjsk_intel_deliveries"
                " WHERE report_id = ? AND target = ?",
                (report_id, target),
            )
            row = await cursor.fetchone()
            attempts = (row["attempts"] if row else 0) + 1
            now = int(time.time())
            if attempts >= MAX_ATTEMPTS:
                status = "permanent_failed"
                delay = 0
            else:
                status = "retry"
                delay = (
                    RETRY_BACKOFF_SECONDS[min(attempts - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
                    if attempts <= len(RETRY_BACKOFF_SECONDS)
                    else RETRY_CEILING_SECONDS
                )
            await connection.execute(
                "UPDATE pjsk_intel_deliveries SET status = ?, attempts = ?,"
                " next_retry_at = ?, updated_at = ? WHERE report_id = ? AND target = ?",
                (status, attempts, now + delay, now, report_id, target),
            )
            await connection.execute(
                "UPDATE pjsk_intel_sources SET last_error = ?,"
                " consecutive_failures = consecutive_failures + 1"
                " WHERE source = (SELECT source FROM pjsk_intel_reports"
                " WHERE report_id = ?)",
                (error, report_id),
            )
            await connection.commit()

    # -- 图片过期清理（§11.2、§11.3）---------------------------------------------
    async def expire_images(self) -> int:
        now = int(time.time())
        async with self._connect() as connection:
            cursor = await connection.execute(
                "SELECT report_id, image_path FROM pjsk_intel_reports"
                " WHERE image_path IS NOT NULL AND image_expires_at > 0"
                " AND image_expires_at < ?",
                (now,),
            )
            rows = await cursor.fetchall()
        removed = 0
        for report_id, image_path in rows:
            path = Path(image_path)
            if path.is_file():
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    continue
            async with self._connect() as connection:
                await connection.execute(
                    "UPDATE pjsk_intel_reports SET image_path = NULL,"
                    " image_expires_at = 0 WHERE report_id = ?",
                    (report_id,),
                )
                await connection.commit()
        return removed


def _counts(report: dict) -> dict:
    objects = report.get("objects") or []
    counts: dict[str, int] = {}
    for obj in objects:
        counts[obj["entity_type"]] = counts.get(obj["entity_type"], 0) + 1
    if report.get("corrections"):
        counts["correction"] = len(report["corrections"])
    return counts


async def pump_deliveries(store: PjskIntelStore, settings: Settings) -> None:
    """发布队列泵（§12.5、§14.1）：租约领取 → 推 AkitoBot → 记录回执。

    进程崩溃后过期租约被新一轮接管；图片过期后只发文字摘要。
    """
    last_expire = 0.0
    while True:
        try:
            claim = await store.claim_delivery()
            if claim is None:
                if time.time() - last_expire > 3600:
                    await store.expire_images()
                    last_expire = time.time()
                await asyncio.sleep(15)
                continue
            report_id, target, summary = claim
            image_path = await store.report_image(report_id)
            payload: dict = {"report_id": report_id, "summary": summary, "image_png_base64": None}
            if image_path is not None:
                data = Path(image_path).read_bytes()
                if len(data) <= 9 * 1024 * 1024:
                    payload["image_png_base64"] = base64.b64encode(data).decode("ascii")
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    settings.pjsk_intel_push_url,
                    json=payload,
                    headers={"Authorization": "Bearer %s" % settings.pjsk_intel_push_token},
                )
            body = response.json()
            if response.status_code == 200 and body.get("ok"):
                await store.finish_delivery(report_id, target, str(body.get("message_id") or ""))
            elif response.status_code == 409:
                await asyncio.sleep(5)  # Bot 侧同报告仍在发送中，稍后再试。
            else:
                await store.retry_delivery(
                    report_id, target, "bot %d: %s" % (response.status_code, body.get("detail"))
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - 泵必须永不退出
            logger.exception("pjsk-intel 投递泵异常：%s", error)
            await asyncio.sleep(30)


def _report_row(row) -> dict | None:
    if row is None:
        return None
    # r.* 的列序：report_id, run_id, source, title, summary, counts,
    # public_json, image_path, image_sha256, image_bytes, image_expires_at,
    # created_at, 之后是联表出来的 delivery_status, message_id。
    return {
        "report_id": row[0],
        "title": row[3],
        "summary": row[4],
        "counts": json.loads(row[5] or "{}"),
        "public": json.loads(row[6] or "{}"),
        "image_sha256": row[8],
        "image_bytes": row[9],
        "image_expires_at": row[10],
        "created_at": row[11],
        "delivery_status": row[12] if len(row) > 12 else None,
        "message_id": row[13] if len(row) > 13 else None,
        "has_image": bool(row[7]) and int(row[10] or 0) >= time.time(),
    }


def _history_row(row) -> dict:
    return {
        "report_id": row[0],
        "title": row[1],
        "summary": row[2],
        "counts": json.loads(row[3] or "{}"),
        "created_at": row[4],
        "delivery_status": row[5],
        "message_id": row[6],
    }
