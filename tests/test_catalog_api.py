"""The three catalog endpoints: stored capture state, projections, error mapping."""

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
                        "userMysekaiCharacterTalks": [
                            {"mysekaiCharacterTalkId": 10, "isRead": False}
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
    state = Haruki()
    app = create_app(_settings(tmp_path))
    service = app.state.services.mysekai
    service.transport = state.transport()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            yield client, state, service


async def test_fixture_list_uses_the_last_capture_state(harness):
    # Given the account uploaded a capture that carried its blueprints
    client, state, service = harness
    await service.repository.set_mysekai_catalog_state(
        server="cn",
        game_user_id=GAME_ID,
        music_records=None,
        blueprints=[{"mysekaiBlueprintId": 1}],
    )
    # When the bot asks for the craftable catalog
    response = await client.post(
        "/internal/v1/mysekai/fixture-list", json={"game_user_id": GAME_ID}, headers=AUTH
    )
    # Then it renders from the stored rows without touching Haruki at all
    assert response.status_code == 200
    assert response.content.startswith(b"\x89PNG")
    assert state.projections == []
    assert state.render_payloads[0]["user"]["userMysekaiBlueprints"] == [
        {"mysekaiBlueprintId": 1}
    ]
    # 资料卡第二行展示数据更新时间，由存储时间格式化而来
    assert state.render_payloads[0]["profile"]["updated_at"]


async def test_fixture_list_without_a_capture_is_a_409(harness):
    # Given an account that never uploaded anything
    client, _, _ = harness
    # When the catalog command runs
    response = await client.post(
        "/internal/v1/mysekai/fixture-list", json={"game_user_id": GAME_ID}, headers=AUTH
    )
    # Then the bot can tell the user to upload first
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "catalog_state_missing"


async def test_talk_list_reads_haruki_and_omits_the_unavailable_blueprint_field(harness):
    # Given the public suite does not expose userMysekaiBlueprints
    client, state, _ = harness
    # When the talk command asks for its projection
    response = await client.post(
        "/internal/v1/mysekai/talk-list",
        json={"game_user_id": GAME_ID, "query": "初音 ln all"},
        headers=AUTH,
    )
    # Then the read happens against Haruki without that key, and the options
    # are forwarded verbatim
    assert response.status_code == 200
    assert "userMysekaiCharacterTalks" in state.projections[0]
    # 这两个字段都不在公开白名单里，带任何一个都会让整组请求 404
    assert "userMysekaiBlueprints" not in state.projections[0]
    assert "userMysekaiGamedata" not in state.projections[0]
    assert state.render_payloads[0]["query"] == "初音 ln all"


async def test_a_second_talk_call_reuses_the_snapshot_when_the_version_matches(harness):
    # Given one talk call already cached the payload
    client, state, _ = harness
    body = {"game_user_id": GAME_ID, "query": "冬弥"}
    await client.post("/internal/v1/mysekai/talk-list", json=body, headers=AUTH)
    # When the same account asks again with the same upload_time
    await client.post("/internal/v1/mysekai/talk-list", json=body, headers=AUTH)
    # Then only the tiny version probe runs, not a second projection fetch
    assert len(state.projections) == 1
    assert state.probes == 2


async def test_a_stale_snapshot_is_reported_as_capture_stale(harness):
    # Given Haruki rejects the projected read
    client, state, _ = harness
    state.suite_status = 404
    # When the bot asks for the character talk list
    response = await client.post(
        "/internal/v1/mysekai/talk-list",
        json={"game_user_id": GAME_ID, "query": "冬弥"},
        headers=AUTH,
    )
    # Then the bot can tell the user the data is unavailable
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "capture_stale"


async def test_an_unknown_character_surfaces_as_422(harness):
    # Given the render service rejects the character token
    client, state, _ = harness
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


async def test_music_record_uses_the_last_capture_state(harness):
    # Given a capture that carried the record table, with show_id requested
    client, state, service = harness
    await service.repository.set_mysekai_catalog_state(
        server="cn",
        game_user_id=GAME_ID,
        music_records=[{"mysekaiMusicRecordId": 184, "obtainedAt": 5}],
        blueprints=None,
    )
    # When the bot asks for the collection card
    response = await client.post(
        "/internal/v1/mysekai/music-record",
        json={"game_user_id": GAME_ID, "show_id": True},
        headers=AUTH,
    )
    # Then the stored rows reach the renderer, and no Haruki call happened
    assert response.status_code == 200
    assert state.projections == []
    assert state.render_payloads[0]["show_id"] is True
    assert state.render_payloads[0]["user"]["userMysekaiMusicRecords"] == [
        {"mysekaiMusicRecordId": 184, "obtainedAt": 5}
    ]


async def test_music_record_without_record_rows_is_a_409(harness):
    # Given a capture that never refreshed the record table
    client, _, service = harness
    await service.repository.set_mysekai_catalog_state(
        server="cn", game_user_id=GAME_ID, music_records=None, blueprints=[]
    )
    # When the record command runs
    response = await client.post(
        "/internal/v1/mysekai/music-record", json={"game_user_id": GAME_ID}, headers=AUTH
    )
    # Then the user is told to upload again rather than shown an empty card
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "catalog_state_missing"


async def test_catalog_endpoints_require_the_bot_token(harness):
    # Given a caller without the bearer token
    client, _, _ = harness
    # When it tries the catalog endpoint
    response = await client.post(
        "/internal/v1/mysekai/fixture-list", json={"game_user_id": GAME_ID}
    )
    # Then it is refused before touching storage
    assert response.status_code == 403


async def test_catalog_link_is_signed_and_opens_the_card(harness):
    # Given an uploaded capture with blueprints
    client, _, service = harness
    await service.repository.set_mysekai_catalog_state(
        server="cn",
        game_user_id=GAME_ID,
        music_records=None,
        blueprints=[{"mysekaiBlueprintId": 1}],
    )
    # When the bot asks for a link instead of an image
    response = await client.post(
        "/internal/v1/mysekai/catalog-link",
        json={"game_user_id": GAME_ID, "kind": "fixture-list"},
        headers=AUTH,
    )
    # Then the URL opens the same card without a session
    assert response.status_code == 200
    url = response.json()["url"]
    assert url.startswith("http://testserver/api/v1/mysekai/catalog/fixture-list?t=")
    opened = await client.get(url.split("testserver", 1)[1])
    assert opened.status_code == 200
    assert opened.content[:1] == bytes([0x89])


async def test_a_forged_catalog_link_is_refused(harness):
    # Given a token nobody signed
    client, _, _ = harness
    # When the browser asks for the card
    response = await client.get("/api/v1/mysekai/catalog/fixture-list?t=abc.def")
    # Then it is refused before any lookup
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_link"
