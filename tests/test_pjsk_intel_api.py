"""pjsk-intel：入库幂等、鉴权、report_id 校验和图片端点（§13、§12）。"""

from __future__ import annotations

import base64
import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.main import create_app

from test_mysekai import TINY_PNG, _mock_transport, _settings

INGEST_TOKEN = "intel-ingest-token"
BOT_TOKEN = "intel-bot-token"


def _intel_settings(tmp_path: Path) -> Settings:
    base = _settings(tmp_path)
    return replace(
        base,
        pjsk_intel_ingest_token=INGEST_TOKEN,
        pjsk_intel_bot_token=BOT_TOKEN,
        pjsk_intel_push_url="",
        pjsk_intel_push_token="",
        pjsk_intel_image_dir=tmp_path / "intel-images",
        pjsk_intel_image_ttl_days=7,
        pjsk_intel_target_group="default",
    )


def _report(source: str = "master:sc", to_sha: str = "b" * 40) -> dict:
    return {
        "schema": "intel-report-v1",
        "source": source,
        "region": "sc",
        "from_sha": "a" * 40,
        "to_sha": to_sha,
        "detected_at": 1735689600000,
        "generated_at": 1735689700000,
        "title": "国服 Master 更新",
        "summary": "1 项更新",
        "objects": [
            {
                "entity_type": "music",
                "entity_id": "music:900",
                "change_type": "added",
                "title": "新歌",
                "starts_at": 0,
                "ends_at": 0,
                "summary": "新歌曲",
                "details": ["开放时间：…"],
                "assets": [
                    {"kind": "music_jacket", "name": "jacket_s_900", "hash": "x" * 64, "missing": False}
                ],
                "completeness": 1.0,
            }
        ],
        "corrections": [],
        "unclassified_count": 0,
        "excluded_count": 0,
    }


def _rid(report: dict) -> str:
    canonical = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@asynccontextmanager
async def _app(tmp_path: Path):
    app = create_app(_intel_settings(tmp_path))
    app.state.services.mysekai.transport = _mock_transport()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            yield client


def _ingest_body(report: dict, image: bytes | None = TINY_PNG) -> dict:
    return {
        "report_id": _rid(report),
        "run_id": "run-1",
        "report": report,
        "audit": {"diff_summary": {"musics.json": {"added": 1}}},
        "image_png_base64": base64.b64encode(image).decode("ascii") if image else "",
    }


async def test_ingest_then_duplicate(tmp_path) -> None:
    async with _app(tmp_path) as client:
        headers = {"Authorization": "Bearer %s" % INGEST_TOKEN}
        first = await client.post(
            "/internal/v1/pjsk-intel/ingest", json=_ingest_body(_report()), headers=headers
        )
        assert first.status_code == 200, first.text
        assert first.json()["duplicate"] is False

        again = await client.post(
            "/internal/v1/pjsk-intel/ingest", json=_ingest_body(_report()), headers=headers
        )
        assert again.status_code == 200
        assert again.json()["duplicate"] is True

        latest = await client.get(
            "/internal/v1/pjsk-intel/latest", headers={"Authorization": "Bearer %s" % BOT_TOKEN}
        )
        assert latest.status_code == 200
        payload = latest.json()
        assert payload["has_image"] is True
        assert payload["counts"]["music"] == 1


async def test_ingest_rejects_bad_token_and_bad_report_id(tmp_path) -> None:
    async with _app(tmp_path) as client:
        body = _ingest_body(_report())
        no_auth = await client.post("/internal/v1/pjsk-intel/ingest", json=body)
        assert no_auth.status_code == 403

        wrong_token = await client.post(
            "/internal/v1/pjsk-intel/ingest",
            json=body,
            headers={"Authorization": "Bearer nope"},
        )
        assert wrong_token.status_code == 403

        tampered = _ingest_body(_report())
        tampered["report_id"] = "c" * 64
        tampered = await client.post(
            "/internal/v1/pjsk-intel/ingest",
            json=tampered,
            headers={"Authorization": "Bearer %s" % INGEST_TOKEN},
        )
        assert tampered.status_code == 422


async def test_image_served_and_gone_after_expiry(tmp_path) -> None:
    async with _app(tmp_path) as client:
        headers = {"Authorization": "Bearer %s" % INGEST_TOKEN}
        report = _report()
        rid = _rid(report)
        await client.post(
            "/internal/v1/pjsk-intel/ingest", json=_ingest_body(report), headers=headers
        )
        bot_headers = {"Authorization": "Bearer %s" % BOT_TOKEN}
        image = await client.get(
            "/internal/v1/pjsk-intel/image/%s" % rid, headers=bot_headers
        )
        assert image.status_code == 200
        assert image.content.startswith(b"\x89PNG")

        # 直接把过期时间写死到过去，再查应 404（图片过期只留文字日志 §11.3）。
        import sqlite3

        database = tmp_path / "test.sqlite3"
        connection = sqlite3.connect(database)
        connection.execute(
            "UPDATE pjsk_intel_reports SET image_expires_at = 1 WHERE report_id = ?", (rid,)
        )
        connection.commit()
        connection.close()
        gone = await client.get(
            "/internal/v1/pjsk-intel/image/%s" % rid, headers=bot_headers
        )
        assert gone.status_code == 404

        latest = await client.get("/internal/v1/pjsk-intel/latest", headers=bot_headers)
        assert latest.json()["has_image"] is False


async def test_run_status_and_source_state(tmp_path) -> None:
    async with _app(tmp_path) as client:
        headers = {"Authorization": "Bearer %s" % INGEST_TOKEN}
        body = {
            "source": "master:sc",
            "run_id": "run-42",
            "from_sha": "a" * 40,
            "to_sha": "b" * 40,
            "status": "published",
            "error": None,
        }
        response = await client.post(
            "/internal/v1/pjsk-intel/run-status", json=body, headers=headers
        )
        assert response.status_code == 200

        state = await client.get(
            "/internal/v1/pjsk-intel/source-state", headers=headers
        )
        assert state.status_code == 200
        rows = state.json()
        assert rows[0]["published_sha"] == "b" * 40


async def test_history_lists_reports(tmp_path) -> None:
    async with _app(tmp_path) as client:
        headers = {"Authorization": "Bearer %s" % INGEST_TOKEN}
        for sha in ("b" * 40, "c" * 40):
            report = _report(to_sha=sha)
            await client.post(
                "/internal/v1/pjsk-intel/ingest", json=_ingest_body(report), headers=headers
            )
        history = await client.get(
            "/internal/v1/pjsk-intel/history?limit=5",
            headers={"Authorization": "Bearer %s" % BOT_TOKEN},
        )
        assert history.status_code == 200
        rows = history.json()
        assert len(rows) == 2
        assert all(row["title"] == "国服 Master 更新" for row in rows)
