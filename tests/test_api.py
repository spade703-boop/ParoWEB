from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    base = Settings.from_env()
    return replace(
        base,
        database_path=tmp_path / "test.sqlite3",
        cookie_secure=False,
        allowed_hosts=("testserver",),
        allowed_origins=("http://testserver",),
        draw_cooldown_seconds=0,
    )


@pytest.fixture
async def client(settings: Settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as api_client:
            yield api_client


async def test_session_catalog_draw_profile_and_clear(client: httpx.AsyncClient) -> None:
    session = await client.post("/api/v1/session", json={}, headers={"Origin": "http://testserver"})
    assert session.status_code == 200
    assert "paro_visitor=" in session.headers["set-cookie"]
    assert "HttpOnly" in session.headers["set-cookie"]

    catalog = await client.get("/api/v1/catalog")
    assert catalog.status_code == 200
    assert catalog.json()["akito"]
    fixed_name = catalog.json()["akito"][0]["name"]

    draw = await client.post(
        "/api/v1/draw",
        json={"count": 3, "fixed_side": "akito", "fixed_name": fixed_name},
        headers={"Origin": "http://testserver"},
    )
    assert draw.status_code == 200
    assert len(draw.json()["results"]) == 3

    profile = await client.get("/api/v1/me")
    assert profile.json()["draw_count"] == 3
    assert len(profile.json()["recent"]) == 3

    cleared = await client.delete("/api/v1/me/history", headers={"Origin": "http://testserver"})
    assert cleared.status_code == 200
    assert (await client.get("/api/v1/me")).json()["draw_count"] == 0


async def test_validation_origin_and_content_type(client: httpx.AsyncClient) -> None:
    invalid = await client.post(
        "/api/v1/draw",
        json={"count": 4, "fixed_side": "none", "fixed_name": None},
        headers={"Origin": "http://testserver"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_request"

    wrong_origin = await client.post(
        "/api/v1/draw",
        json={"count": 1, "fixed_side": "none", "fixed_name": None},
        headers={"Origin": "https://example.com"},
    )
    assert wrong_origin.status_code == 403

    wrong_type = await client.post(
        "/api/v1/draw",
        content="{}",
        headers={"Origin": "http://testserver", "Content-Type": "text/plain"},
    )
    assert wrong_type.status_code == 415


async def test_health_and_database_does_not_store_raw_cookie(client: httpx.AsyncClient, settings: Settings) -> None:
    response = await client.post("/api/v1/session", json={}, headers={"Origin": "http://testserver"})
    raw_token = response.cookies.get("paro_visitor")
    database_bytes = settings.database_path.read_bytes()
    assert raw_token.encode() not in database_bytes
    assert (await client.get("/healthz")).status_code == 200


async def test_homepage_exposes_image_export_and_blob_csp(client: httpx.AsyncClient) -> None:
    homepage = await client.get("/")
    assert homepage.status_code == 200
    assert "/static/export-image.js" in homepage.text
    assert "img-src 'self' data: blob:" in homepage.headers["content-security-policy"]

    exporter = await client.get("/static/export-image.js")
    assert exporter.status_code == 200
    assert "ParoImageExporter" in exporter.text


async def test_announcements_are_available(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/announcements")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["version"]
    assert payload["items"]
    assert {"id", "published_at", "title", "summary", "details", "tags", "highlight"} <= set(payload["items"][0])


async def test_community_stats_are_aggregated(client: httpx.AsyncClient) -> None:
    empty = await client.get("/api/v1/community-stats")
    assert empty.status_code == 200
    assert empty.headers["cache-control"] == "no-store"
    assert empty.json() == {
        "total_draws": 0,
        "random_draws": 0,
        "akito_top": [],
        "toya_top": [],
        "pair_top": [],
    }

    catalog = (await client.get("/api/v1/catalog")).json()
    fixed_name = catalog["akito"][0]["name"]
    draw = await client.post(
        "/api/v1/draw",
        json={"count": 3, "fixed_side": "akito", "fixed_name": fixed_name},
        headers={"Origin": "http://testserver"},
    )
    assert draw.status_code == 200

    stats = (await client.get("/api/v1/community-stats")).json()
    assert stats["total_draws"] == 3
    assert stats["random_draws"] == 0
    assert stats["akito_top"] == []
    assert stats["toya_top"] == []

    random_draw = await client.post(
        "/api/v1/draw",
        json={"count": 3, "fixed_side": "none", "fixed_name": None},
        headers={"Origin": "http://testserver"},
    )
    assert random_draw.status_code == 200
    stats = (await client.get("/api/v1/community-stats")).json()
    assert stats["total_draws"] == 6
    assert stats["random_draws"] == 3
    assert len(stats["pair_top"]) <= 10

    personal = await client.get("/api/v1/me/stats")
    assert personal.status_code == 200
    assert personal.json()["total_draws"] == 6
    assert personal.json()["random_draws"] == 3


async def test_adaptive_draw_cooldown_uses_daily_tiers(settings: Settings) -> None:
    limited_settings = replace(settings, draw_cooldown_seconds=20)
    app = create_app(limited_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as api_client:
            headers = {"Origin": "http://testserver"}
            first = await api_client.post(
                "/api/v1/draw",
                json={"count": 1, "fixed_side": "none", "fixed_name": None},
                headers=headers,
            )
            assert first.status_code == 200
            blocked = await api_client.post(
                "/api/v1/draw",
                json={"count": 1, "fixed_side": "none", "fixed_name": None},
                headers=headers,
            )
            assert blocked.status_code == 429
            assert blocked.json()["error"]["code"] == "draw_limited"
            assert 1 <= int(blocked.headers["retry-after"]) <= 20

            cleared = await api_client.delete("/api/v1/me/history", headers=headers)
            assert cleared.status_code == 200
            still_blocked = await api_client.post(
                "/api/v1/draw",
                json={"count": 1, "fixed_side": "none", "fixed_name": None},
                headers=headers,
            )
            assert still_blocked.status_code == 429

            repository = app.state.services.repository
            async with repository.connect() as connection:
                visitor = await (await connection.execute("SELECT id FROM visitors LIMIT 1")).fetchone()
                await connection.execute(
                    "UPDATE draw_limits SET last_draw_at = ?, daily_results = 200 WHERE visitor_id = ?",
                    (datetime.now(UTC).isoformat().replace("+00:00", "Z"), visitor["id"]),
                )
                await connection.commit()
            tier_blocked = await api_client.post(
                "/api/v1/draw",
                json={"count": 1, "fixed_side": "none", "fixed_name": None},
                headers=headers,
            )
            assert tier_blocked.status_code == 429
            assert 598 <= int(tier_blocked.headers["retry-after"]) <= 600

            async with repository.connect() as connection:
                await connection.execute(
                    "UPDATE draw_limits SET quota_day = ?, last_draw_at = ?, daily_results = 499 WHERE visitor_id = ?",
                    ("2000-01-01", datetime.now(UTC).isoformat().replace("+00:00", "Z"), visitor["id"]),
                )
                await connection.commit()
            reset_draw = await api_client.post(
                "/api/v1/draw",
                json={"count": 1, "fixed_side": "none", "fixed_name": None},
                headers=headers,
            )
            assert reset_draw.status_code == 200


async def test_concurrent_draws_are_all_persisted(client: httpx.AsyncClient) -> None:
    await client.post("/api/v1/session", json={}, headers={"Origin": "http://testserver"})
    responses = await asyncio.gather(
        *[
            client.post(
                "/api/v1/draw",
                json={"count": 3, "fixed_side": "none", "fixed_name": None},
                headers={"Origin": "http://testserver"},
            )
            for _ in range(5)
        ]
    )
    assert all(response.status_code == 200 for response in responses)
    profile = await client.get("/api/v1/me")
    assert profile.json()["draw_count"] == 15
    first_page = await client.get("/api/v1/me?recent_limit=5")
    second_page = await client.get("/api/v1/me?recent_offset=5&recent_limit=5")
    assert first_page.json()["recent_total"] == 15
    assert first_page.json()["recent_has_more"] is True
    assert len(first_page.json()["recent"]) == 5
    assert len(second_page.json()["recent"]) == 5
    assert {item["id"] for item in first_page.json()["recent"]}.isdisjoint(
        item["id"] for item in second_page.json()["recent"]
    )


async def test_mutations_require_allowed_origin(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/session", json={})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_origin"


async def test_unknown_api_uses_error_contract(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/missing")
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "接口不存在", "details": {}}
    }
