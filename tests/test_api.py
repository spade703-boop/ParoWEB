from __future__ import annotations

import asyncio
from dataclasses import replace

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
