from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys


DEFAULT_START = "2026-09-24T00:00:00Z"
DEFAULT_END = "2026-09-27T00:00:00Z"
DEFAULT_PEAK_RESULTS_PER_HOUR = 300
CONFIRMATION = "DELETE_FLAGGED_BATCHES"
CHINA_TZ = timezone(timedelta(hours=8))
SIDE_LABELS = {"akito": "彰人方", "toya": "冬弥方"}


@dataclass(frozen=True, slots=True)
class CleanupCriteria:
    start: str
    end: str
    peak_results_per_hour: int


@dataclass(frozen=True, slots=True)
class FlaggedGroup:
    visitor_id: str
    fixed_side: str
    fixed_name: str | None
    result_count: int
    peak_results_per_hour: int
    batch_count: int
    first_created_at: str
    last_created_at: str


@dataclass(frozen=True, slots=True)
class CandidateBatch:
    id: str
    visitor_id: str
    fixed_side: str
    fixed_name: str | None
    created_at: str
    result_count: int


@dataclass(frozen=True, slots=True)
class CleanupPlan:
    criteria: CleanupCriteria
    groups: tuple[FlaggedGroup, ...]
    batches: tuple[CandidateBatch, ...]

    @property
    def visitor_count(self) -> int:
        return len({group.visitor_id for group in self.groups})

    @property
    def result_count(self) -> int:
        return sum(batch.result_count for batch in self.batches)


FLAGGED_CTE = """
WITH hourly AS (
    SELECT b.visitor_id,
           b.fixed_side,
           b.fixed_name,
           substr(b.created_at, 1, 13) AS utc_hour,
           COUNT(r.id) AS result_count
    FROM draw_batches b
    JOIN draw_results r ON r.batch_id = b.id
    WHERE b.fixed_side <> 'none'
      AND b.created_at >= ?
      AND b.created_at < ?
    GROUP BY b.visitor_id, b.fixed_side, b.fixed_name, utc_hour
),
flagged AS (
    SELECT visitor_id,
           fixed_side,
           fixed_name,
           SUM(result_count) AS result_count,
           MAX(result_count) AS peak_results_per_hour
    FROM hourly
    GROUP BY visitor_id, fixed_side, fixed_name
    HAVING MAX(result_count) >= ?
)
"""


def normalize_timestamp(raw: str) -> str:
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"无效时间：{raw}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"时间必须带时区：{raw}")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_timestamp(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def china_timestamp(raw: str) -> str:
    return parse_timestamp(raw).astimezone(CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S")


def results_by_china_date(plan: CleanupPlan) -> dict[str, int]:
    totals: Counter[str] = Counter()
    for batch in plan.batches:
        day = parse_timestamp(batch.created_at).astimezone(CHINA_TZ).date().isoformat()
        totals[day] += batch.result_count
    return dict(sorted(totals.items()))


def validate_criteria(criteria: CleanupCriteria) -> None:
    if criteria.start >= criteria.end:
        raise ValueError("开始时间必须早于结束时间")
    if criteria.peak_results_per_hour < 1:
        raise ValueError("每小时结果阈值必须大于 0")


def connect(database_path: Path, *, read_only: bool) -> sqlite3.Connection:
    if not database_path.is_file():
        raise FileNotFoundError(f"数据库不存在：{database_path}")
    connection = sqlite3.connect(database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA foreign_keys = ON")
    if read_only:
        connection.execute("PRAGMA query_only = ON")
    return connection


def build_cleanup_plan(connection: sqlite3.Connection, criteria: CleanupCriteria) -> CleanupPlan:
    validate_criteria(criteria)
    parameters = (criteria.start, criteria.end, criteria.peak_results_per_hour)
    group_rows = connection.execute(
        FLAGGED_CTE
        + """
        SELECT f.visitor_id,
               f.fixed_side,
               f.fixed_name,
               f.result_count,
               f.peak_results_per_hour,
               COUNT(DISTINCT b.id) AS batch_count,
               MIN(b.created_at) AS first_created_at,
               MAX(b.created_at) AS last_created_at
        FROM flagged f
        JOIN draw_batches b
          ON b.visitor_id = f.visitor_id
         AND b.fixed_side = f.fixed_side
         AND b.fixed_name IS f.fixed_name
        WHERE b.created_at >= ?
          AND b.created_at < ?
        GROUP BY f.visitor_id, f.fixed_side, f.fixed_name,
                 f.result_count, f.peak_results_per_hour
        ORDER BY f.result_count DESC, f.visitor_id, f.fixed_side, f.fixed_name
        """,
        parameters + (criteria.start, criteria.end),
    ).fetchall()
    batch_rows = connection.execute(
        FLAGGED_CTE
        + """
        SELECT b.id,
               b.visitor_id,
               b.fixed_side,
               b.fixed_name,
               b.created_at,
               COUNT(r.id) AS result_count
        FROM flagged f
        JOIN draw_batches b
          ON b.visitor_id = f.visitor_id
         AND b.fixed_side = f.fixed_side
         AND b.fixed_name IS f.fixed_name
        JOIN draw_results r ON r.batch_id = b.id
        WHERE b.created_at >= ?
          AND b.created_at < ?
        GROUP BY b.id, b.visitor_id, b.fixed_side, b.fixed_name, b.created_at
        ORDER BY b.created_at, b.id
        """,
        parameters + (criteria.start, criteria.end),
    ).fetchall()
    return CleanupPlan(
        criteria=criteria,
        groups=tuple(FlaggedGroup(**dict(row)) for row in group_rows),
        batches=tuple(CandidateBatch(**dict(row)) for row in batch_rows),
    )


def create_backup(database_path: Path, backup_path: Path) -> str:
    if backup_path.exists():
        raise FileExistsError(f"备份文件已存在：{backup_path}")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(database_path, read_only=True) as source:
        if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("源数据库 quick_check 未通过，已停止清理")
        with sqlite3.connect(backup_path) as target:
            source.backup(target)
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("备份数据库 quick_check 未通过，已停止清理")
    digest = hashlib.sha256()
    with backup_path.open("rb") as backup_file:
        for chunk in iter(lambda: backup_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_output_paths(database_path: Path, run_at: datetime) -> tuple[Path, Path]:
    stamp = run_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = Path(f"{database_path}-pre-launch-cleanup-{stamp}")
    manifest_path = Path(f"{database_path}-cleanup-{stamp}.json")
    return backup_path, manifest_path


def manifest_payload(
    plan: CleanupPlan,
    *,
    database_path: Path,
    backup_path: Path,
    backup_sha256: str,
    run_at: datetime,
) -> dict:
    return {
        "run_at": run_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "database": str(database_path),
        "backup": str(backup_path),
        "backup_sha256": backup_sha256,
        "criteria": asdict(plan.criteria),
        "summary": {
            "visitor_count": plan.visitor_count,
            "group_count": len(plan.groups),
            "batch_count": len(plan.batches),
            "result_count": plan.result_count,
            "results_by_china_date": results_by_china_date(plan),
        },
        "groups": [asdict(group) for group in plan.groups],
        "batch_ids": [batch.id for batch in plan.batches],
    }


def apply_cleanup(
    database_path: Path,
    plan: CleanupPlan,
    *,
    backup_path: Path,
    manifest_path: Path,
    run_at: datetime,
) -> dict:
    if not plan.batches:
        return {"deleted_batches": 0, "deleted_results": 0}
    if manifest_path.exists():
        raise FileExistsError(f"清单文件已存在：{manifest_path}")

    backup_sha256 = create_backup(database_path, backup_path)
    payload = manifest_payload(
        plan,
        database_path=database_path,
        backup_path=backup_path,
        backup_sha256=backup_sha256,
        run_at=run_at,
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_manifest = Path(f"{manifest_path}.tmp")
    if temporary_manifest.exists():
        raise FileExistsError(f"临时清单已存在：{temporary_manifest}")
    temporary_manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    try:
        with connect(database_path, read_only=False) as connection:
            connection.execute("BEGIN IMMEDIATE")
            locked_plan = build_cleanup_plan(connection, plan.criteria)
            expected_ids = {batch.id for batch in plan.batches}
            locked_ids = {batch.id for batch in locked_plan.batches}
            if locked_ids != expected_ids:
                connection.rollback()
                raise RuntimeError("备份后候选批次发生变化，已停止清理；请重新预览")

            connection.execute("CREATE TEMP TABLE cleanup_target_batches (id TEXT PRIMARY KEY)")
            connection.executemany(
                "INSERT INTO cleanup_target_batches(id) VALUES (?)",
                ((batch_id,) for batch_id in sorted(expected_ids)),
            )
            result_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM draw_results r
                JOIN cleanup_target_batches t ON t.id = r.batch_id
                """
            ).fetchone()[0]
            deleted = connection.execute(
                "DELETE FROM draw_batches WHERE id IN (SELECT id FROM cleanup_target_batches)"
            ).rowcount
            remaining_results = connection.execute(
                """
                SELECT COUNT(*)
                FROM draw_results r
                JOIN cleanup_target_batches t ON t.id = r.batch_id
                """
            ).fetchone()[0]
            if deleted != len(expected_ids) or remaining_results:
                connection.rollback()
                raise RuntimeError("级联删除校验失败，事务已回滚")
            connection.commit()
        os.replace(temporary_manifest, manifest_path)
    except Exception:
        temporary_manifest.unlink(missing_ok=True)
        raise

    return {"deleted_batches": deleted, "deleted_results": result_count}


def print_plan(plan: CleanupPlan, database_path: Path) -> None:
    print("上线初期固定派生异常清理预览")
    print(f"数据库：{database_path}")
    print(f"时间范围：[{plan.criteria.start}, {plan.criteria.end})")
    print(
        "规则：同一匿名访客 + 固定方 + 固定名称，"
        f"任一 UTC 整点小时达到 {plan.criteria.peak_results_per_hour} 条结果"
    )
    print(
        f"命中：{plan.visitor_count} 个访客，{len(plan.groups)} 个组合，"
        f"{len(plan.batches)} 个批次，{plan.result_count} 条结果"
    )
    if plan.batches:
        daily_text = "，".join(
            f"{day}：{count} 条" for day, count in results_by_china_date(plan).items()
        )
        print(f"按北京时间日期汇总：{daily_text}")
    for index, group in enumerate(plan.groups, 1):
        fixed_side = SIDE_LABELS.get(group.fixed_side, group.fixed_side)
        print(f"\n{index:>2}. 匿名访客：{group.visitor_id}")
        print(f"    固定选择：{fixed_side} · {group.fixed_name or '（名称为空）'}")
        print(
            "    记录时间（北京时间）："
            f"{china_timestamp(group.first_created_at)} ～ {china_timestamp(group.last_created_at)}"
        )
        print(
            f"    抽取结果：{group.result_count} 条；抽取批次：{group.batch_count} 次；"
            f"单个 UTC 整点小时峰值：{group.peak_results_per_hour} 条"
        )


def parser() -> argparse.ArgumentParser:
    default_database = Path(os.getenv("PARO_DATABASE_PATH", "data/paro_web.sqlite3"))
    command = argparse.ArgumentParser(
        description="预览或清理网站上线初期固定派生的异常高频批次。默认只读，不会修改数据库。"
    )
    command.add_argument("--database", type=Path, default=default_database)
    command.add_argument("--start", default=DEFAULT_START, help=f"含起点，默认 {DEFAULT_START}")
    command.add_argument("--end", default=DEFAULT_END, help=f"不含终点，默认 {DEFAULT_END}")
    command.add_argument(
        "--peak-results-per-hour",
        type=int,
        default=DEFAULT_PEAK_RESULTS_PER_HOUR,
        help=f"同一固定组合单个 UTC 小时的结果阈值，默认 {DEFAULT_PEAK_RESULTS_PER_HOUR}",
    )
    command.add_argument("--apply", action="store_true", help="备份后执行删除；不传时仅预览")
    command.add_argument("--confirm", help=f"执行时必须同时传入 {CONFIRMATION}")
    command.add_argument("--backup", type=Path, help="自定义完整 SQLite 备份路径")
    command.add_argument("--manifest", type=Path, help="自定义 JSON 审计清单路径")
    return command


def main(argv: list[str] | None = None) -> int:
    command = parser()
    arguments = command.parse_args(argv)
    try:
        criteria = CleanupCriteria(
            start=normalize_timestamp(arguments.start),
            end=normalize_timestamp(arguments.end),
            peak_results_per_hour=arguments.peak_results_per_hour,
        )
        database_path = arguments.database.resolve()
        with connect(database_path, read_only=True) as connection:
            plan = build_cleanup_plan(connection, criteria)
        print_plan(plan, database_path)

        if not arguments.apply:
            print(f"\n当前为只读预览。确认无误后追加：--apply --confirm {CONFIRMATION}")
            return 0
        if arguments.confirm != CONFIRMATION:
            raise ValueError(f"执行清理必须传入 --confirm {CONFIRMATION}")
        if not plan.batches:
            print("\n没有命中记录，无需清理。")
            return 0

        run_at = datetime.now(UTC)
        default_backup, default_manifest = default_output_paths(database_path, run_at)
        backup_path = (arguments.backup or default_backup).resolve()
        manifest_path = (arguments.manifest or default_manifest).resolve()
        result = apply_cleanup(
            database_path,
            plan,
            backup_path=backup_path,
            manifest_path=manifest_path,
            run_at=run_at,
        )
        print(f"\n已删除 {result['deleted_batches']} 个批次、{result['deleted_results']} 条结果。")
        print(f"完整备份：{backup_path}")
        print(f"审计清单：{manifest_path}")
        print("访客和 Cookie 映射未删除；用户刷新个人记录页后会读取清理后的数据。")
        return 0
    except (FileNotFoundError, FileExistsError, RuntimeError, ValueError, sqlite3.Error) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
