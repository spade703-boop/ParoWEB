"""Sync endpoints: start, proxy status, and clear caches when the job ends."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.main import create_app

TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
BOT_TOKEN = "bot-token"
AUTH = {"Authorization": f"Bearer {BOT_TOKEN}"}


class SyncUpstream:
    """Stand-in for the render service's /sync endpoints."""

    def __init__(self) -> None:
        self.starts = 0
        self.start_status = 200
        self.start_result = {"job_id": "abc123", "already_running": False, "state": "running"}
        self.status = {"id": "abc123", "state": "running", "steps": []}
        self.status_requests = 0

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/sync":
                self.starts += 1
                assert request.headers.get("X-Mysekai-Admin") == BOT_TOKEN
                return httpx.Response(self.start_status, json=self.start_result)
            if request.url.path == "/sync/abc123":
                self.status_requests += 1
                return httpx.Response(200, json=self.status)
            return httpx.Response(404, json={"error": "not found"})

        return httpx.MockTransport(handler)


def _settings(tmp_path: Path) -> Settings:
    base = Settings.from_env()
    return replace(
        base,
        announcements_path=tmp_path / "announcements.json",
        database_path=tmp_path / "test.sqlite3",
        cookie_secure=False,
        allowed_hosts=("testserver",),
        allowed_origins=("http://testserver",),
        public_base_url="http://testserver",
        draw_cooldown_seconds=0,
        mysekai_password="secret",
        mysekai_session_secret="session-secret",
        mysekai_service_url="http://mysekai.test",
        mysekai_haruki_base="https://haruki.test",
        mysekai_asset_base="https://assets.test",
        mysekai_master_base="https://master.test",
        mysekai_internal_token=BOT_TOKEN,
        mysekai_push_url="",
        mysekai_push_token="",
    )


@pytest.fixture
async def harness(tmp_path):
    state = SyncUpstream()
    app = create_app(_settings(tmp_path))
    service = app.state.services.mysekai
    service.transport = state.transport()
    service.sync_poll_seconds = 0.01
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            yield client, state, service


async def test_start_sync_forwards_and_returns_job_id(harness):
    client, state, _ = harness
    response = await client.post("/internal/v1/mysekai/sync", json={}, headers=AUTH)

    assert response.status_code == 200
    assert response.json()["job_id"] == "abc123"
    assert state.starts == 1


async def test_start_sync_requires_the_bot_token(harness):
    client, state, _ = harness
    response = await client.post("/internal/v1/mysekai/sync", json={})
    assert response.status_code == 403
    assert state.starts == 0


async def test_sync_status_proxies(harness):
    client, state, _ = harness
    state.status = {"id": "abc123", "state": "running", "steps": []}
    response = await client.get("/internal/v1/mysekai/sync/abc123", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["state"] == "running"


async def test_completion_clears_cached_cards(harness):
    client, state, service = harness
    service._materials_cache = [{"id": 1}]
    service._material_card_cache = b"old"
    state.status = {"id": "abc123", "state": "succeeded", "steps": []}

    await client.post("/internal/v1/mysekai/sync", json={}, headers=AUTH)

    for _ in range(200):
        if service._materials_cache is None and service._material_card_cache is None:
            break
        await asyncio.sleep(0.01)
    assert service._materials_cache is None
    assert service._material_card_cache is None
