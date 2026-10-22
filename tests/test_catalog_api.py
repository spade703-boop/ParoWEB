"""The three catalog endpoints: projection, version cache, and error mapping."""

from __future__ import annotations

import base64
import json
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
GAME_ID = "7440000000000000000"
BOT_TOKEN = "bot-token"
AUTH = {"Authorization": f"Bearer {BOT_TOKEN}"}


class Haruki:
    """Mutable stand-in for Haruki plus the render service."""

    def __init__(self) -> None:
        self.version = 111
        self.suite_status = 200
        self.projections: list[str] = []
        self.probes = 0
        self.render_payloads: list[dict] = []
        self.render_status = 200
        self.render_error: dict | None = None

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.startswith("/api/public/cn/suite/"):
                key = request.url.params.get("key", "")
                if key == "upload_time":
                    self.probes += 1
                    return httpx.Response(200, json={"upload_time": self.version})
                self.projections.append(key)
                if self.suite_status != 200:
                    return httpx.Response(self.suite_status, json={"error": "nope"})
                return httpx.Response(
                    200,
                    json={
                        "upload_time": self.version,
                        "userGamedata": {"userId": int(GAME_ID), "name": "测试玩家"},
                        "userMysekaiBlueprints": [{"mysekaiBlueprintId": 1}],
                        "userMysekaiCharacterTalks": [
                            {"mysekaiCharacterTalkId": 10, "isRead": False}
                        ],
                        "userMysekaiMusicRecords": [
                            {"mysekaiMusicRecordId": 184, "obtainedAt": 5}
                        ],
                    },
                )
            if path.startswith("/render/mysekai/"):
                self.render_payloads.append(json.loads(request.content))
                if self.render_status != 200:
                    return httpx.Response(self.render_status, json=self.render_error or {})
                return httpx.Response(
                    200, content=TINY_PNG, headers={"content-type": "image/png"}
                )
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
    state = Haruki()
    app = create_app(_settings(tmp_path))
    app.state.services.mysekai.transport = state.transport()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            yield client, state


async def test_fixture_list_requests_the_projection_and_returns_a_png(harness):
    # Given a bot call for the craftable catalog
    client, state = harness
    # When it hits the internal endpoint
    response = await client.post(
        "/internal/v1/mysekai/fixture-list", json={"game_user_id": GAME_ID}, headers=AUTH
    )
    # Then the snapshot projection carries the identity and version keys
    assert response.status_code == 200
    assert response.content.startswith(b"\x89PNG")
    assert len(state.projections) == 1
    projection = state.projections[0]
    assert "userMysekaiBlueprints" in projection
    assert "userGamedata" in projection
    assert "upload_time" in projection
    assert state.render_payloads[0]["user"]["userMysekaiBlueprints"]


async def test_a_second_call_reuses_the_snapshot_when_the_version_matches(harness):
    # Given one successful call already cached the payload
    client, state = harness
    body = {"game_user_id": GAME_ID}
    await client.post("/internal/v1/mysekai/fixture-list", json=body, headers=AUTH)
    # When the same account asks again with the same upload_time
    await client.post("/internal/v1/mysekai/fixture-list", json=body, headers=AUTH)
    # Then only the tiny version probe runs, not a second projection fetch
    assert len(state.projections) == 1
    assert state.probes == 2


async def test_talk_list_forwards_the_character_options(harness):
    # Given a command with a character, a unit and the all flag
    client, state = harness
    response = await client.post(
        "/internal/v1/mysekai/talk-list",
        json={"game_user_id": GAME_ID, "query": "初音 ln all"},
        headers=AUTH,
    )
    # Then the render service receives exactly those options
    assert response.status_code == 200
    assert state.render_payloads[0]["query"] == "初音 ln all"


async def test_talk_projection_omits_the_unavailable_blueprint_field(harness):
    # Given the public suite does not expose userMysekaiBlueprints at all
    client, state = harness
    # When the talk command asks for its projection
    await client.post(
        "/internal/v1/mysekai/talk-list",
        json={"game_user_id": GAME_ID, "query": "冬弥"},
        headers=AUTH,
    )
    # Then the request does not include it, so the whole read does not 404
    assert "userMysekaiCharacterTalks" in state.projections[0]
    assert "userMysekaiBlueprints" not in state.projections[0]


async def test_a_stale_snapshot_is_reported_as_capture_stale(harness):
    # Given Haruki rejects the projected read
    client, state = harness
    state.suite_status = 404
    # When the bot asks for the record list
    response = await client.post(
        "/internal/v1/mysekai/music-record", json={"game_user_id": GAME_ID}, headers=AUTH
    )
    # Then the bot can tell the user to re-upload rather than showing an empty card
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "capture_stale"


async def test_an_unknown_character_surfaces_as_422(harness):
    # Given the render service rejects the character token
    client, state = harness
    state.render_status = 422
    state.render_error = {"error": "character_unknown", "message": "未知角色"}
    # When the bot asks for that character
    response = await client.post(
        "/internal/v1/mysekai/talk-list",
        json={"game_user_id": GAME_ID, "query": "不存在"},
        headers=AUTH,
    )
    # Then the typed code survives the hop
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "character_unknown"


async def test_music_record_forwards_show_id(harness):
    # Given the id switch typed after the command
    client, state = harness
    await client.post(
        "/internal/v1/mysekai/music-record",
        json={"game_user_id": GAME_ID, "show_id": True},
        headers=AUTH,
    )
    # Then the render payload asks for the ids
    assert state.render_payloads[0]["show_id"] is True


async def test_catalog_endpoints_require_the_bot_token(harness):
    # Given a caller without the bearer token
    client, _ = harness
    # When it tries the catalog endpoint
    response = await client.post(
        "/internal/v1/mysekai/fixture-list", json={"game_user_id": GAME_ID}
    )
    # Then it is refused before touching Haruki
    assert response.status_code == 403
