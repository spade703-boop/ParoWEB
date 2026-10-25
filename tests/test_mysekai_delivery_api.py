"""The internal birthday progress endpoint returns the strip PNG."""

from __future__ import annotations

import base64
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.main import create_app

QQ = "123456789"
GAME = "7440000000000000000"
BOT_TOKEN = "bot-token"
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _settings(tmp_path: Path) -> Settings:
    base = Settings.from_env()
    return replace(
        base,
        announcements_path=tmp_path / "announcements.json",
        database_path=tmp_path / "test.sqlite3",
        cookie_secure=False,
        allowed_hosts=("testserver",),
        allowed_origins=("http://testserver",),
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


def _transport(render_status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/render/birthday":
            if render_status != 200:
                return httpx.Response(render_status, json={"error": "no_active_birthday"})
            return httpx.Response(
                200, content=TINY_PNG, headers={"content-type": "image/png"}
            )
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler)


@pytest.fixture
async def api_client(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings)
    app.state.services.mysekai.transport = _transport()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            yield client, app


async def _bind_and_store(app, obtained: int = 25) -> None:
    repository = app.state.services.mysekai.repository
    await repository.create_mysekai_binding(
        qq=QQ,
        server="cn",
        game_user_id=GAME,
        nickname=None,
        deck_id=None,
        leader_card_id=None,
        leader_avatar=None,
        profile_image_type=None,
    )
    await repository.set_mysekai_birthday_state(
        server="cn",
        game_user_id=GAME,
        party_id=2,
        character_id=3,
        obtained=obtained,
        dropped=0,
        total_point=241290,
        captured_at_ms=1792911813696,
    )


async def test_birthday_progress_returns_png(api_client) -> None:
    client, app = api_client
    await _bind_and_store(app)

    response = await client.post(
        "/internal/v1/mysekai/birthday-progress",
        json={"qq": QQ},
        headers={"Authorization": f"Bearer {BOT_TOKEN}"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == TINY_PNG


async def test_without_state_returns_404(api_client) -> None:
    client, app = api_client
    repository = app.state.services.mysekai.repository
    await repository.create_mysekai_binding(
        qq=QQ, server="cn", game_user_id=GAME, nickname=None, deck_id=None,
        leader_card_id=None, leader_avatar=None, profile_image_type=None,
    )

    response = await client.post(
        "/internal/v1/mysekai/birthday-progress",
        json={"qq": QQ},
        headers={"Authorization": f"Bearer {BOT_TOKEN}"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "birthday_state_missing"


async def test_no_active_party_maps_to_404(api_client) -> None:
    client, app = api_client
    await _bind_and_store(app)
    app.state.services.mysekai.transport = _transport(render_status=404)

    response = await client.post(
        "/internal/v1/mysekai/birthday-progress",
        json={"qq": QQ},
        headers={"Authorization": f"Bearer {BOT_TOKEN}"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "no_active_birthday"


def test_ingest_response_for_a_delivery_has_no_snapshot_fields() -> None:
    from app.web.mysekai_api import _ingest_response

    out = _ingest_response({
        "kind": "birthday_delivery",
        "upload_id": "u1",
        "message": "本轮无稀有材料",
        "push": {"status": "skipped", "message": "生日刷新仅发送监听结果"},
        "watch": {"status": "recorded", "message": "本轮已记录"},
    })

    assert out["kind"] == "birthday_delivery"
    assert "points" not in out and "sites" not in out and "drops" not in out


def test_ingest_response_for_a_snapshot_keeps_the_snapshot_fields() -> None:
    from app.web.mysekai_api import _ingest_response

    out = _ingest_response({
        "upload_id": "u2",
        "points": 3,
        "drops": 4,
        "sites": [{"site_id": 5}],
        "push": {"status": "skipped", "message": ""},
        "watch": None,
    })

    assert out["points"] == 3
    assert out["drops"] == 4
    assert out["sites"] == [{"site_id": 5}]
