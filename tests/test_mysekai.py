from __future__ import annotations

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
GAME_ID = "7440000000000000000"
QQ = "123456789"
BOT_TOKEN = "bot-token"


def _suite_payload() -> dict:
    return {
        "userGamedata": {
            "userId": int(GAME_ID),
            "name": "测试玩家",
            "deck": 3,
            "rank": 419,
        },
        "userProfile": {
            "userId": int(GAME_ID),
            "word": "",
            "twitterId": "",
            "profileImageType": "leader",
        },
        "userDecks": [
            {"deckId": 1, "leader": 1146},
            {"deckId": 3, "leader": 456},
        ],
        "userCards": [{"cardId": 456, "defaultImage": "original"}],
    }


def _mock_transport(*, push_ok: bool = True) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == f"/api/public/cn/suite/{GAME_ID}":
            return httpx.Response(200, json=_suite_payload())
        if path == "/v1/master/cn/files/cards.json":
            return httpx.Response(
                200, json=[{"id": 456, "assetbundleName": "res003_no019"}]
            )
        if path in {
            "/cn-assets/startapp/thumbnail/chara/res003_no019_normal.png",
            "/cn-assets/ondemand/thumbnail/chara/res003_no019_normal.png",
            "/cn-assets/startapp/character/member/res003_no019/card_normal.png",
        }:
            return httpx.Response(
                200, content=TINY_PNG, headers={"content-type": "image/png"}
            )
        if path == "/submit":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "sites": [{"site_id": 5, "points": 33, "drops": 177}],
                    "points": 33,
                    "drops": 177,
                    "map_png_base64": base64.b64encode(TINY_PNG).decode(),
                    "card_png_base64": base64.b64encode(TINY_PNG).decode(),
                },
            )
        if path == "/push":
            return httpx.Response(200 if push_ok else 500, json={"ok": push_ok})
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler)


def _settings(tmp_path: Path, **overrides) -> Settings:
    base = Settings.from_env()
    values = dict(
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
        mysekai_bot_token=BOT_TOKEN,
        mysekai_push_url="",
        mysekai_push_token="",
    )
    values.update(overrides)
    return replace(base, **values)


@pytest.fixture
async def client(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings)
    app.state.services.mysekai.transport = _mock_transport()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            timeout=30,
        ) as api_client:
            yield api_client


async def _login(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/mysekai/auth",
        json={"password": "secret"},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 200


async def _bind(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/mysekai/bind",
        json={"qq": QQ, "game_user_id": GAME_ID, "server": "cn"},
        headers={
            "Origin": "http://testserver",
            "X-Mysekai-Bot-Token": BOT_TOKEN,
        },
    )
    assert response.status_code == 200, response.text


async def test_auth_session_and_wrong_password(client: httpx.AsyncClient) -> None:
    session = await client.get("/api/v1/mysekai/session")
    assert session.json() == {"enabled": True, "authenticated": False}

    wrong = await client.post(
        "/api/v1/mysekai/auth",
        json={"password": "nope"},
        headers={"Origin": "http://testserver"},
    )
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "invalid_password"

    await _login(client)
    session = await client.get("/api/v1/mysekai/session")
    assert session.json() == {"enabled": True, "authenticated": True}


async def test_bind_fetches_public_suite_and_avatar(
    client: httpx.AsyncClient,
) -> None:
    await _bind(client)
    await _login(client)
    lookup = await client.get(f"/api/v1/mysekai/lookup?qq={QQ}")
    assert lookup.status_code == 200
    binding = lookup.json()["binding"]
    assert binding["game_user_id"] == GAME_ID
    assert binding["nickname"] == "测试玩家"
    assert binding["deck_id"] == 3
    assert binding["leader_card_id"] == 456
    assert binding["avatar_data_url"].startswith("data:image/png;base64,")


async def test_bind_requires_bot_token(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/mysekai/bind",
        json={"qq": QQ, "game_user_id": GAME_ID, "server": "cn"},
        headers={"Origin": "http://testserver", "X-Mysekai-Bot-Token": "wrong"},
    )
    assert response.status_code == 403


async def test_submit_rejects_unbound_and_mismatch(
    client: httpx.AsyncClient,
) -> None:
    await _bind(client)
    await _login(client)
    body = base64.b64encode(b"capture").decode()

    mismatch = await client.post(
        "/api/v1/mysekai/submit",
        json={
            "qq": QQ,
            "game_user_id": "7445099373878500999",
            "region": "cn",
            "file_base64": body,
        },
        headers={"Origin": "http://testserver"},
    )
    assert mismatch.status_code == 403
    assert mismatch.json()["error"]["code"] == "binding_mismatch"

    unbound = await client.post(
        "/api/v1/mysekai/submit",
        json={
            "qq": "999999999",
            "game_user_id": GAME_ID,
            "region": "cn",
            "file_base64": body,
        },
        headers={"Origin": "http://testserver"},
    )
    assert unbound.status_code == 403
    assert unbound.json()["error"]["code"] == "not_bound"


async def test_submit_returns_images_and_records_upload(
    client: httpx.AsyncClient,
) -> None:
    await _bind(client)
    await _login(client)
    response = await client.post(
        "/api/v1/mysekai/submit",
        json={
            "qq": QQ,
            "game_user_id": GAME_ID,
            "region": "cn",
            "file_base64": base64.b64encode(b"capture").decode(),
        },
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["profile"]["nickname"] == "测试玩家"
    assert payload["points"] == 33
    assert payload["drops"] == 177
    assert payload["map"].startswith("data:image/png;base64,")
    assert payload["card"].startswith("data:image/png;base64,")
    assert payload["push"]["status"] == "unconfigured"
    assert payload["upload_id"]

    repository = client._transport.app.state.services.repository  # type: ignore[attr-defined]
    async with repository.connect() as connection:
        row = await (
            await connection.execute(
                "SELECT qq, game_user_id, points, drops FROM mysekai_uploads"
            )
        ).fetchone()
    assert dict(row) == {
        "qq": QQ,
        "game_user_id": GAME_ID,
        "points": 33,
        "drops": 177,
    }


async def test_submit_reports_push_success(tmp_path) -> None:
    settings = _settings(tmp_path, mysekai_push_url="http://push.test/push")
    app = create_app(settings)
    app.state.services.mysekai.transport = _mock_transport(push_ok=True)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            await _bind(client)
            await _login(client)
            response = await client.post(
                "/api/v1/mysekai/submit",
                json={
                    "qq": QQ,
                    "game_user_id": GAME_ID,
                    "region": "cn",
                    "file_base64": base64.b64encode(b"capture").decode(),
                },
                headers={"Origin": "http://testserver"},
            )
    assert response.status_code == 200
    assert response.json()["push"]["status"] == "sent"


async def test_page_and_hidden_entry(client: httpx.AsyncClient) -> None:
    page = await client.get("/mysekai")
    assert page.status_code == 200
    assert "神秘入口" in page.text
    assert "输入群公告内的密码" in page.text
    assert "/static/mysekai.js" in page.text
    assert "noindex" in page.text

    index = await client.get("/")
    assert index.status_code == 200
    assert 'href="/mysekai"' in index.text
