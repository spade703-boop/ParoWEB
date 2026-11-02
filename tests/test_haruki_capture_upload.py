"""HarukiProxy 自定义端点 → ParoWEB ingest 的接收路由。"""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx

from app.main import create_app

from test_mysekai import GAME_ID, _bind, _login, _mock_transport, _settings

SECRET = "ingest-token"


@asynccontextmanager
async def _app(tmp_path, *, bind: bool = True):
    settings = _settings(tmp_path, mysekai_ingest_token=SECRET)
    app = create_app(settings)
    app.state.services.mysekai.transport = _mock_transport()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            await _login(client)
            if bind:
                await _bind(client)
            yield client


def _url(kind: str = "mysekai", region: str = "cn", game: str = GAME_ID) -> str:
    return f"/harukiproxy/v3/{region}/{game}/{kind}/upload"


async def test_upload_forwards_the_raw_body_and_reports_the_upload_id(tmp_path) -> None:
    async with _app(tmp_path) as client:
        response = await client.post(
            _url(),
            content=b"capture",
            headers={"X-Haruki-Toolbox-Secret": SECRET},
        )

    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    assert response.json()["upload_id"]


async def test_a_wrong_secret_is_rejected(tmp_path) -> None:
    async with _app(tmp_path) as client:
        response = await client.post(
            _url(), content=b"capture", headers={"X-Haruki-Toolbox-Secret": "nope"}
        )

    assert response.status_code == 403


async def test_birthday_delivery_uploads_are_accepted(tmp_path) -> None:
    async with _app(tmp_path) as client:
        response = await client.post(
            _url(kind="mysekai_birthday_party"),
            content=b"capture",
            headers={"X-Haruki-Toolbox-Secret": SECRET},
        )

    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True


async def test_capture_kinds_other_than_mysekai_are_refused(tmp_path) -> None:
    async with _app(tmp_path) as client:
        response = await client.post(
            _url(kind="suite"),
            content=b"capture",
            headers={"X-Haruki-Toolbox-Secret": SECRET},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_capture"


async def test_only_the_cn_region_is_accepted(tmp_path) -> None:
    async with _app(tmp_path) as client:
        response = await client.post(
            _url(region="jp"),
            content=b"capture",
            headers={"X-Haruki-Toolbox-Secret": SECRET},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_region"


async def test_an_unbound_game_id_is_refused(tmp_path) -> None:
    async with _app(tmp_path, bind=False) as client:
        response = await client.post(
            _url(), content=b"capture", headers={"X-Haruki-Toolbox-Secret": SECRET}
        )

    assert response.status_code in {403, 404}
