from __future__ import annotations

import base64
from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from app.repositories.sqlite import SQLiteRepository


TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
GAME_ID = "7440000000000000000"
SECOND_GAME_ID = "7440000000000000001"
QQ = "123456789"
BOT_TOKEN = "bot-token"


def _suite_payload(game_user_id: str = GAME_ID, nickname: str = "测试玩家") -> dict:
    return {
        "userGamedata": {
            "userId": int(game_user_id),
            "name": nickname,
            "deck": 3,
            "rank": 419,
        },
        "userProfile": {
            "userId": int(game_user_id),
            "word": "",
            "twitterId": "",
            "profileImageType": "leader",
        },
        "userDecks": [
            {"deckId": 1, "leader": 1146},
            {"deckId": 3, "leader": 456},
        ],
        "userCards": [{"cardId": 456, "defaultImage": "original"}],
        "userMysekaiGates": [
            {"mysekaiGateId": gate_id, "mysekaiGateLevel": 10 + gate_id}
            for gate_id in range(1, 6)
        ],
        "userMysekaiMaterials": [
            {"mysekaiMaterialId": 1, "quantity": 5},
            {"mysekaiMaterialId": 6, "quantity": 2},
        ],
    }


def _mock_transport(
    *,
    push_ok: bool = True,
    haruki_statuses: list[int] | None = None,
    nicknames: list[str] | None = None,
    push_payloads: list[dict] | None = None,
    suite_requests: list[dict] | None = None,
    suite_rejects_key: bool = False,
    render_payloads: list[dict] | None = None,
    render_status: int = 200,
    submit_birthday: dict | None = None,
    convert_state: dict | None = None,
    submit_headers: list[dict] | None = None,
    submit_visit_counts: dict | None = None,
) -> httpx.MockTransport:
    suite_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal suite_calls
        path = request.url.path
        if path.startswith("/api/public/cn/suite/"):
            if suite_requests is not None:
                suite_requests.append(dict(request.url.params))
            if suite_rejects_key and "key" in request.url.params:
                return httpx.Response(404, json={"error": "key not allowed"})
            game_user_id = path.rsplit("/", 1)[-1]
            index = suite_calls
            suite_calls += 1
            status = (
                haruki_statuses[min(index, len(haruki_statuses) - 1)]
                if haruki_statuses
                else 200
            )
            if status != 200:
                return httpx.Response(status, json={"error": "Haruki error"})
            nickname = (
                nicknames[min(index, len(nicknames) - 1)]
                if nicknames
                else "测试玩家"
            )
            return httpx.Response(
                200, json=_suite_payload(game_user_id, nickname)
            )
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
        if path == "/render/gate":
            if render_payloads is not None:
                render_payloads.append(json.loads(request.content))
            if render_status != 200:
                return httpx.Response(render_status, json={"error": "gate_max_level"})
            return httpx.Response(
                200, content=TINY_PNG, headers={"content-type": "image/png"}
            )
        if path == "/submit":
            if submit_headers is not None:
                submit_headers.append(dict(request.headers))
            body = {
                "ok": True,
                "sites": [{"site_id": 5, "points": 33, "drops": 177}],
                "points": 33,
                "drops": 177,
                "map_png_base64": base64.b64encode(TINY_PNG).decode(),
                "card_png_base64": base64.b64encode(TINY_PNG).decode(),
            }
            if submit_birthday is not None:
                body["birthday"] = submit_birthday
            if submit_visit_counts is not None:
                body["visit_counts"] = submit_visit_counts
            return httpx.Response(200, json=body)
        if path == "/convert-state":
            if convert_state is None:
                return httpx.Response(422, json={"error": "no convert state"})
            return httpx.Response(200, json=convert_state)
        if path == "/push":
            if push_payloads is not None:
                push_payloads.append(json.loads(request.content))
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
        mysekai_internal_token=BOT_TOKEN,
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
        "/internal/v1/mysekai/bind",
        json={"qq": QQ, "game_user_id": GAME_ID, "server": "cn"},
        headers={"Authorization": f"Bearer {BOT_TOKEN}"},
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
    lookup = await client.get(
        f"/internal/v1/mysekai/query?qq={QQ}",
        headers={"Authorization": f"Bearer {BOT_TOKEN}"},
    )
    assert lookup.status_code == 200
    binding = lookup.json()["binding"]
    assert binding["game_user_id"] == GAME_ID
    assert binding["nickname"] == "测试玩家"
    assert binding["deck_id"] == 3
    assert binding["leader_card_id"] == 456
    assert binding["avatar_data_url"].startswith("data:image/png;base64,")


async def test_bind_requires_bot_token(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/internal/v1/mysekai/bind",
        json={"qq": QQ, "game_user_id": GAME_ID, "server": "cn"},
        headers={"Authorization": "Bearer wrong"},
    )
    assert response.status_code == 403


async def test_public_lookup_is_not_available(client: httpx.AsyncClient) -> None:
    await _bind(client)
    await _login(client)
    response = await client.get(f"/api/v1/mysekai/lookup?qq={QQ}")
    assert response.status_code == 404


async def test_a_qq_can_bind_several_roles(client: httpx.AsyncClient) -> None:
    await _bind(client)
    headers = {"Authorization": f"Bearer {BOT_TOKEN}"}

    second = await client.post(
        "/internal/v1/mysekai/bind",
        json={"qq": QQ, "game_user_id": SECOND_GAME_ID, "server": "cn"},
        headers=headers,
    )
    assert second.status_code == 200
    assert second.json()["binding"]["game_user_id"] == SECOND_GAME_ID

    same_role = await client.post(
        "/internal/v1/mysekai/bind",
        json={"qq": QQ, "game_user_id": GAME_ID, "server": "cn"},
        headers=headers,
    )
    assert same_role.status_code == 409
    assert same_role.json()["error"]["code"] == "already_bound"

    same_game = await client.post(
        "/internal/v1/mysekai/bind",
        json={"qq": "987654321", "game_user_id": GAME_ID, "server": "cn"},
        headers=headers,
    )
    assert same_game.status_code == 409
    assert same_game.json()["error"]["code"] == "game_id_already_bound"

    query = await client.get(f"/internal/v1/mysekai/query?qq={QQ}", headers=headers)
    assert query.json()["binding"]["game_user_id"] == GAME_ID
    assert len(query.json()["roles"]) == 2

    switched = await client.post(
        "/internal/v1/mysekai/switch-role",
        json={"qq": QQ, "game_user_id": SECOND_GAME_ID},
        headers=headers,
    )
    assert switched.status_code == 200
    query = await client.get(f"/internal/v1/mysekai/query?qq={QQ}", headers=headers)
    assert query.json()["binding"]["game_user_id"] == SECOND_GAME_ID

    unbound = await client.post(
        "/internal/v1/mysekai/unbind",
        json={"qq": QQ, "game_user_id": SECOND_GAME_ID},
        headers=headers,
    )
    assert unbound.status_code == 200
    query = await client.get(f"/internal/v1/mysekai/query?qq={QQ}", headers=headers)
    assert query.json()["binding"]["game_user_id"] == GAME_ID
    assert len(query.json()["roles"]) == 1

    await client.post(
        "/internal/v1/mysekai/unbind", json={"qq": QQ}, headers=headers
    )
    rebound = await client.post(
        "/internal/v1/mysekai/bind",
        json={"qq": "987654321", "game_user_id": GAME_ID, "server": "cn"},
        headers=headers,
    )
    assert rebound.status_code == 200


async def test_a_qq_stops_at_three_roles(client: httpx.AsyncClient) -> None:
    await _bind(client)
    headers = {"Authorization": f"Bearer {BOT_TOKEN}"}
    for game_id in ("7440000000000000001", "7440000000000000002"):
        response = await client.post(
            "/internal/v1/mysekai/bind",
            json={"qq": QQ, "game_user_id": game_id, "server": "cn"},
            headers=headers,
        )
        assert response.status_code == 200, response.text

    fourth = await client.post(
        "/internal/v1/mysekai/bind",
        json={"qq": QQ, "game_user_id": "7440000000000000003", "server": "cn"},
        headers=headers,
    )
    assert fourth.status_code == 409
    assert fourth.json()["error"]["code"] == "role_limit_reached"


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


async def test_visit_baseline_is_keyed_by_game_day(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    """基线按游戏日（05:00 → 次日 04:59）分区，17:00 的刷新不清基线。

    早上传过、下午再传仍然用同一条基线；到次日 05:00 才重记
    （2026-10-31 用户说明：每个角色一天只能拿一份记忆）。
    """
    import app.services.mysekai as mysekai_module

    service = client._transport.app.state.services.mysekai  # type: ignore[attr-defined]
    service.transport = _mock_transport(submit_visit_counts={"1": 4})
    await _bind(client)
    await _login(client)

    days = iter(["2026-10-23", "2026-10-23", "2026-10-24"])
    monkeypatch.setattr(mysekai_module, "_game_day_key", lambda: next(days))

    for _ in range(3):
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

    repository = client._transport.app.state.services.repository  # type: ignore[attr-defined]
    async with repository.connect() as connection:
        rows = await (
            await connection.execute(
                "SELECT day FROM mysekai_visit_baselines ORDER BY day"
            )
        ).fetchall()
    assert [row["day"] for row in rows] == ["2026-10-23", "2026-10-24"]


async def test_submit_records_the_role_that_uploaded(
    client: httpx.AsyncClient,
) -> None:
    """A second role must not be filed under the QQ's current role."""
    await _bind(client)
    headers = {"Authorization": f"Bearer {BOT_TOKEN}"}
    second = await client.post(
        "/internal/v1/mysekai/bind",
        json={"qq": QQ, "game_user_id": SECOND_GAME_ID, "server": "cn"},
        headers=headers,
    )
    assert second.status_code == 200, second.text
    await _login(client)
    response = await client.post(
        "/api/v1/mysekai/submit",
        json={
            "qq": QQ,
            "game_user_id": SECOND_GAME_ID,
            "region": "cn",
            "file_base64": base64.b64encode(b"capture").decode(),
        },
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 200, response.text
    upload_id = response.json()["upload_id"]

    repository = client._transport.app.state.services.repository  # type: ignore[attr-defined]
    async with repository.connect() as connection:
        row = await (
            await connection.execute(
                "SELECT game_user_id FROM mysekai_uploads WHERE id = ?",
                (upload_id,),
            )
        ).fetchone()
    assert row["game_user_id"] == SECOND_GAME_ID


async def test_submit_reports_push_success(tmp_path) -> None:
    push_payloads: list[dict] = []
    settings = _settings(tmp_path, mysekai_push_url="http://push.test/push")
    app = create_app(settings)
    app.state.services.mysekai.transport = _mock_transport(
        push_ok=True, push_payloads=push_payloads
    )
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
    assert push_payloads[0]["upload_id"] == response.json()["upload_id"]


async def test_push_labels_the_uploading_role(tmp_path) -> None:
    """The push must carry the role that uploaded, not the QQ's current role."""
    push_payloads: list[dict] = []
    settings = _settings(tmp_path, mysekai_push_url="http://push.test/push")
    app = create_app(settings)
    app.state.services.mysekai.transport = _mock_transport(
        push_ok=True, push_payloads=push_payloads
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            await _bind(client)
            headers = {"Authorization": f"Bearer {BOT_TOKEN}"}
            second = await client.post(
                "/internal/v1/mysekai/bind",
                json={"qq": QQ, "game_user_id": SECOND_GAME_ID, "server": "cn"},
                headers=headers,
            )
            assert second.status_code == 200, second.text
            await _login(client)
            response = await client.post(
                "/api/v1/mysekai/submit",
                json={
                    "qq": QQ,
                    "game_user_id": SECOND_GAME_ID,
                    "region": "cn",
                    "file_base64": base64.b64encode(b"capture").decode(),
                },
                headers={"Origin": "http://testserver"},
            )
    assert response.status_code == 200, response.text
    assert response.json()["push"]["status"] == "sent"
    assert push_payloads[0]["role_label"] == "u2"
    assert push_payloads[0]["game_user_id"] == SECOND_GAME_ID


async def test_submit_refreshes_profile(tmp_path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    app.state.services.mysekai.transport = _mock_transport(
        nicknames=["绑定昵称", "上传昵称"]
    )
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
    assert response.json()["profile"]["nickname"] == "上传昵称"


@pytest.mark.parametrize("refresh_status, expected", [(500, 200), (404, 404)])
async def test_submit_haruki_refresh_fallback(
    tmp_path, refresh_status: int, expected: int
) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    app.state.services.mysekai.transport = _mock_transport(
        haruki_statuses=[200, refresh_status]
    )
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
    assert response.status_code == expected


async def test_page_and_hidden_entry(client: httpx.AsyncClient) -> None:
    page = await client.get("/roomlist")
    assert page.status_code == 200
    assert "神秘入口" in page.text
    assert "输入群公告内的密码" in page.text
    assert "/static/mysekai.js" in page.text
    assert "noindex" in page.text

    script = await client.get("/static/mysekai.js")
    assert "/lookup" not in script.text
    assert "paro_mysekai_qq" in script.text
    assert "paro_mysekai_gid" in script.text

    # 入口不再出现在首页，旧地址也不保留跳转，避免被爬虫顺着找到新路径
    index = await client.get("/")
    assert index.status_code == 200
    assert 'href="/mysekai"' not in index.text
    assert 'href="/roomlist"' not in index.text
    assert (await client.get("/mysekai")).status_code == 404


# ---------------------------------------------------------------- ingest（移动端上传）

INGEST_TOKEN = "ingest-token"


async def _ingest(
    client: httpx.AsyncClient,
    *,
    body: bytes = b"capture",
    game_user_id: str = GAME_ID,
    token: str | None = INGEST_TOKEN,
):
    headers = {"Content-Type": "application/octet-stream", "X-Mysekai-Game-Id": game_user_id}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return await client.post("/api/v1/mysekai/ingest", content=body, headers=headers)


async def test_ingest_renders_and_pushes_without_a_session(tmp_path) -> None:
    push_payloads: list[dict] = []
    settings = _settings(
        tmp_path,
        mysekai_ingest_token=INGEST_TOKEN,
        mysekai_push_url="http://push.test/push",
    )
    app = create_app(settings)
    app.state.services.mysekai.transport = _mock_transport(push_payloads=push_payloads)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            await _bind(client)
            response = await _ingest(client)

    assert response.status_code == 200
    payload = response.json()
    assert payload["points"] == 33
    assert payload["push"]["status"] == "sent"
    assert "map" not in payload and "card" not in payload
    assert push_payloads[0]["upload_id"] == payload["upload_id"]
    assert push_payloads[0]["qq"] == QQ
    assert "message" not in push_payloads[0]


async def test_ingest_requires_its_token(tmp_path) -> None:
    settings = _settings(tmp_path, mysekai_ingest_token=INGEST_TOKEN)
    app = create_app(settings)
    app.state.services.mysekai.transport = _mock_transport()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            missing = await _ingest(client, token=None)
            wrong = await _ingest(client, token="nope")

    assert missing.status_code == 403
    assert wrong.status_code == 403


async def test_ingest_is_disabled_without_a_token(tmp_path) -> None:
    settings = _settings(tmp_path, mysekai_ingest_token="")
    app = create_app(settings)
    app.state.services.mysekai.transport = _mock_transport()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            response = await _ingest(client)

    assert response.status_code == 503


async def test_ingest_rejects_unbound_game_id(tmp_path) -> None:
    settings = _settings(tmp_path, mysekai_ingest_token=INGEST_TOKEN)
    app = create_app(settings)
    app.state.services.mysekai.transport = _mock_transport()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            response = await _ingest(client, game_user_id=SECOND_GAME_ID)

    assert response.status_code == 403


async def test_ingest_validates_header_and_body(tmp_path) -> None:
    settings = _settings(tmp_path, mysekai_ingest_token=INGEST_TOKEN)
    app = create_app(settings)
    app.state.services.mysekai.transport = _mock_transport()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            bad_id = await _ingest(client, game_user_id="123")
            empty = await _ingest(client, body=b"")

    assert bad_id.status_code == 422
    assert empty.status_code == 422


async def test_ingest_honours_the_allowlist(tmp_path) -> None:
    settings = _settings(
        tmp_path,
        mysekai_ingest_token=INGEST_TOKEN,
        mysekai_ingest_allowlist=frozenset({SECOND_GAME_ID}),
    )
    app = create_app(settings)
    app.state.services.mysekai.transport = _mock_transport()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            response = await _ingest(client, game_user_id=GAME_ID)

    assert response.status_code == 403


# ---------------------------------------------------------------- 模块下载

async def test_module_download_requires_a_session(tmp_path) -> None:
    settings = _settings(tmp_path, mysekai_ingest_token=INGEST_TOKEN)
    app = create_app(settings)
    app.state.services.mysekai.transport = _mock_transport()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            response = await client.get("/api/v1/mysekai/module")

    assert response.status_code == 303
    assert response.headers["location"] == "/roomlist"


async def test_module_download_bakes_in_the_token(tmp_path) -> None:
    settings = _settings(tmp_path, mysekai_ingest_token=INGEST_TOKEN)
    app = create_app(settings)
    app.state.services.mysekai.transport = _mock_transport()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            await _login(client)
            response = await client.get("/api/v1/mysekai/module")

    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    text = response.text
    assert INGEST_TOKEN in text
    assert "http://testserver/api/v1/mysekai/capture.js" in text
    assert "__TOKEN__" not in text and "__BASE_URL__" not in text
    # 手机端能否拿到二进制 body 取决于这个开关
    assert "binary-body-mode=1" in text
    assert "requires-body=1" in text


async def test_capture_script_needs_the_token(tmp_path) -> None:
    settings = _settings(tmp_path, mysekai_ingest_token=INGEST_TOKEN)
    app = create_app(settings)
    app.state.services.mysekai.transport = _mock_transport()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            denied = await client.get("/api/v1/mysekai/capture.js", params={"t": "wrong"})
            allowed = await client.get(
                "/api/v1/mysekai/capture.js", params={"t": INGEST_TOKEN}
            )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert INGEST_TOKEN in allowed.text
    assert "__TOKEN__" not in allowed.text


async def test_module_can_be_fetched_with_token_without_a_session(tmp_path) -> None:
    settings = _settings(tmp_path, mysekai_ingest_token=INGEST_TOKEN)
    app = create_app(settings)
    app.state.services.mysekai.transport = _mock_transport()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            denied = await client.get(
                "/api/v1/mysekai/module", params={"t": "wrong"}
            )
            allowed = await client.get(
                "/api/v1/mysekai/module", params={"t": INGEST_TOKEN}
            )

    assert denied.status_code == 303
    assert allowed.status_code == 200
    assert INGEST_TOKEN in allowed.text


async def test_module_link_builds_the_shadowrocket_scheme(tmp_path) -> None:
    settings = _settings(tmp_path, mysekai_ingest_token=INGEST_TOKEN)
    app = create_app(settings)
    app.state.services.mysekai.transport = _mock_transport()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            anonymous = await client.get("/api/v1/mysekai/module-link")
            await _login(client)
            response = await client.get("/api/v1/mysekai/module-link")

    assert anonymous.status_code == 401
    assert response.status_code == 200
    data = response.json()
    assert data["install_url"].startswith("shadowrocket://install?module=")
    assert INGEST_TOKEN in data["download_url"]


async def test_visit_baseline_is_written_once_per_game_day(tmp_path) -> None:
    """同一游戏日首次上传留下的基线不能被后续上传覆盖；角色之间也各记各的。"""
    repository = SQLiteRepository(tmp_path / "test.sqlite3")
    await repository.initialize()

    await repository.set_mysekai_visit_baseline(
        QQ, GAME_ID, "2026-10-21", {"1": 5}
    )
    await repository.set_mysekai_visit_baseline(
        QQ, GAME_ID, "2026-10-21", {"1": 9}
    )
    await repository.set_mysekai_visit_baseline(
        QQ, SECOND_GAME_ID, "2026-10-21", {"1": 2}
    )

    assert (
        await repository.get_mysekai_visit_baseline(QQ, GAME_ID, "2026-10-21")
        == {"1": 5}
    )
    assert (
        await repository.get_mysekai_visit_baseline(
            QQ, SECOND_GAME_ID, "2026-10-21"
        )
        == {"1": 2}
    )
    assert (
        await repository.get_mysekai_visit_baseline(QQ, GAME_ID, "2026-10-22")
        is None
    )


async def test_gate_endpoint_requires_internal_token(client) -> None:
    """内部大门长图接口必须校验 Bot 侧 token。"""
    response = await client.post(
        "/internal/v1/mysekai/gate",
        json={"game_user_id": GAME_ID, "server": "cn"},
    )
    assert response.status_code == 403


async def test_water_round_claims_once_per_refreshed_map(tmp_path) -> None:
    """同一刷新轮次只允许推送一次；刷新时间变了才是新一轮。"""
    repository = SQLiteRepository(tmp_path / "test.sqlite3")
    await repository.initialize()

    assert await repository.claim_mysekai_water_round("u1", "t100") is True
    assert await repository.claim_mysekai_water_round("u1", "t100") is False
    assert await repository.claim_mysekai_water_round("u1", "t200") is True


def test_gate_unit_aliases_are_case_insensitive() -> None:
    from app.services.mysekai import GateUnitUnknown, resolve_gate_unit

    assert resolve_gate_unit(None) is None
    assert resolve_gate_unit("  ") is None
    for token in ("LN", "Ln", "ln"):
        assert resolve_gate_unit(token) == 1
    for token in ("MMJ", "mmj"):
        assert resolve_gate_unit(token) == 2
    for token in ("VBS", "vbs"):
        assert resolve_gate_unit(token) == 3
    for token in ("WS", "ws"):
        assert resolve_gate_unit(token) == 4
    for token in ("25", "25H", "25时", "无人"):
        assert resolve_gate_unit(token) == 5
    assert resolve_gate_unit("教室") == 1
    with pytest.raises(GateUnitUnknown):
        resolve_gate_unit("乱写")


async def _gate(client: httpx.AsyncClient, **payload) -> httpx.Response:
    body = {"game_user_id": GAME_ID, "server": "cn", **payload}
    return await client.post(
        "/internal/v1/mysekai/gate",
        json=body,
        headers={"Authorization": f"Bearer {BOT_TOKEN}"},
    )


async def test_gate_fetch_requests_only_the_two_arrays(tmp_path) -> None:
    suite_requests: list[dict] = []
    render_payloads: list[dict] = []
    app = create_app(_settings(tmp_path))
    app.state.services.mysekai.transport = _mock_transport(
        suite_requests=suite_requests, render_payloads=render_payloads
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            response = await _gate(client)

    assert response.status_code == 200
    assert suite_requests[0]["key"] == "userMysekaiGates,userMysekaiMaterials"
    # 没有 unit 时仍是「等级最高且未满级」：门 5 的等级最高
    assert render_payloads[0]["gate_id"] == 5


async def test_gate_fetch_falls_back_when_the_key_filter_is_rejected(tmp_path) -> None:
    suite_requests: list[dict] = []
    render_payloads: list[dict] = []
    app = create_app(_settings(tmp_path))
    app.state.services.mysekai.transport = _mock_transport(
        suite_requests=suite_requests,
        suite_rejects_key=True,
        render_payloads=render_payloads,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            response = await _gate(client)

    assert response.status_code == 200
    assert "key" in suite_requests[0]
    assert "key" not in suite_requests[1]
    assert render_payloads[0]["gate_id"] == 5


async def test_gate_endpoint_explicit_unit_selects_that_gate(tmp_path) -> None:
    render_payloads: list[dict] = []
    app = create_app(_settings(tmp_path))
    app.state.services.mysekai.transport = _mock_transport(
        render_payloads=render_payloads
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            response = await _gate(client, unit="Ln")

    assert response.status_code == 200
    # 等级更低的门 1 也被选中，说明 unit 覆盖了默认选门
    assert render_payloads[0]["gate_id"] == 1


async def test_gate_endpoint_unknown_unit_returns_422(tmp_path) -> None:
    app = create_app(_settings(tmp_path))
    app.state.services.mysekai.transport = _mock_transport()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            response = await _gate(client, unit="乱写")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "gate_unit_unknown"


async def test_gate_endpoint_maps_max_level_to_409(tmp_path) -> None:
    app = create_app(_settings(tmp_path))
    app.state.services.mysekai.transport = _mock_transport(render_status=409)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            response = await _gate(client)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "gate_max_level"


def test_capture_time_reads_seconds_milliseconds_and_iso() -> None:
    # Given the three timestamp shapes different producers store
    from app.services.mysekai import format_capture_time

    # Then seconds and milliseconds describe the same instant
    assert format_capture_time(1792029476) == "2026-10-15 09:57"
    assert format_capture_time(1792029476430) == "2026-10-15 09:57"
    # And an ISO string is still accepted
    assert format_capture_time("2026-10-22T07:01:00+00:00") == "2026-10-22 15:01"
    assert format_capture_time(None) is None


async def test_highlight_endpoints_roundtrip(client, monkeypatch) -> None:
    service = client._transport.app.state.services.mysekai
    monkeypatch.setattr(
        service,
        "fetch_materials",
        AsyncMock(
            return_value=[
                {"id": 8, "name": "铁矿石", "rarity": "rarity_1"},
                {"id": 12, "name": "钻石", "rarity": "rarity_3"},
            ]
        ),
    )
    headers = {"Authorization": f"Bearer {BOT_TOKEN}"}

    set_response = await client.post(
        "/internal/v1/mysekai/highlight-set",
        json={"qq": QQ, "names": ["钻石"]},
        headers=headers,
    )
    assert set_response.status_code == 200
    assert set_response.json()["count"] == 1

    add_response = await client.post(
        "/internal/v1/mysekai/highlight-set",
        json={"qq": QQ, "names": ["铁矿石"], "replace": False},
        headers=headers,
    )
    assert add_response.status_code == 200
    assert add_response.json()["count"] == 2
    assert {row["name"] for row in add_response.json()["materials"]} == {
        "钻石", "铁矿石",
    }

    status_response = await client.post(
        "/internal/v1/mysekai/highlight-status",
        json={"qq": QQ},
        headers=headers,
    )
    assert status_response.status_code == 200
    assert status_response.json()["count"] == 2
    assert {row["name"] for row in status_response.json()["materials"]} == {
        "钻石", "铁矿石",
    }

    remove_response = await client.post(
        "/internal/v1/mysekai/highlight-remove",
        json={"qq": QQ, "names": ["钻石"]},
        headers=headers,
    )
    assert remove_response.status_code == 200
    assert remove_response.json()["count"] == 1


async def test_material_catalog_endpoint_returns_png(client, monkeypatch) -> None:
    service = client._transport.app.state.services.mysekai
    monkeypatch.setattr(
        service, "render_material_catalog", AsyncMock(return_value=TINY_PNG)
    )

    response = await client.post(
        "/internal/v1/mysekai/material-catalog",
        json={},
        headers={"Authorization": f"Bearer {BOT_TOKEN}"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == TINY_PNG
