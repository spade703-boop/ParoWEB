from __future__ import annotations

from contextlib import asynccontextmanager

import httpx

from app.main import create_app

from test_mysekai import INGEST_TOKEN, _mock_transport, _settings


@asynccontextmanager
async def _client(tmp_path):
    app = create_app(_settings(tmp_path, mysekai_ingest_token=INGEST_TOKEN))
    app.state.services.mysekai.transport = _mock_transport()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            timeout=30,
        ) as client:
            yield client


async def test_convert_script_points_at_the_convert_ingest(tmp_path) -> None:
    async with _client(tmp_path) as client:
        response = await client.get(f"/api/v1/mysekai/convert.js?t={INGEST_TOKEN}")

    assert response.status_code == 200
    body = response.text
    assert "/api/v1/mysekai/ingest-convert" in body
    assert "convert" in body
    assert INGEST_TOKEN in body


async def test_convert_script_requires_the_token(tmp_path) -> None:
    async with _client(tmp_path) as client:
        response = await client.get("/api/v1/mysekai/convert.js")

    assert response.status_code == 403


async def test_module_carries_the_convert_rule(tmp_path) -> None:
    async with _client(tmp_path) as client:
        response = await client.get(f"/api/v1/mysekai/module?t={INGEST_TOKEN}")

    assert response.status_code == 200
    body = response.text
    assert r"/mysekai/convert/\d+/(start|receive|shorten)" in body
    assert "/api/v1/mysekai/convert.js" in body
