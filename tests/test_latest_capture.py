from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from app.main import create_app
from app.repositories.sqlite import SQLiteRepository

from test_mysekai import (
    BOT_TOKEN,
    GAME_ID,
    QQ,
    SECOND_GAME_ID,
    TINY_PNG,
    _bind,
    _mock_transport,
    _settings,
)
from test_push_dedupe import _app, _submit

MAP_PNG = b"map-bytes"
CARD_PNG = b"card-bytes"


async def _repository(tmp_path: Path) -> SQLiteRepository:
    repository = SQLiteRepository(tmp_path / "test.sqlite3")
    await repository.initialize()
    return repository


@asynccontextmanager
async def _client(tmp_path: Path):
    """没绑定的空应用，只用来验接口本身。"""
    app = create_app(_settings(tmp_path))
    app.state.services.mysekai.transport = _mock_transport()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            yield client


async def test_latest_capture_roundtrip(tmp_path) -> None:
    repository = await _repository(tmp_path)

    await repository.save_mysekai_latest_capture(
        qq=QQ,
        game_user_id=GAME_ID,
        upload_id="u1",
        captured_at_ms=1792723215029,
        uploaded_at_ms=1792725000000,
        map_png=MAP_PNG,
        card_png=CARD_PNG,
    )
    first = await repository.get_mysekai_latest_capture(QQ, GAME_ID)
    assert first is not None
    assert first["captured_at_ms"] == 1792723215029
    assert first["uploaded_at_ms"] == 1792725000000
    assert first["map_png"] == MAP_PNG
    assert first["card_png"] == CARD_PNG

    # 再传一次必须覆盖，而不是留下两条
    await repository.save_mysekai_latest_capture(
        qq=QQ,
        game_user_id=GAME_ID,
        upload_id="u2",
        captured_at_ms=None,
        uploaded_at_ms=1792728000000,
        map_png=b"map2",
        card_png=b"card2",
    )
    second = await repository.get_mysekai_latest_capture(QQ, GAME_ID)
    assert second is not None
    assert second["captured_at_ms"] is None
    assert second["uploaded_at_ms"] == 1792728000000
    assert second["map_png"] == b"map2"


async def test_latest_capture_is_kept_per_role(tmp_path) -> None:
    """A second role must not overwrite the first role's newest capture."""
    repository = await _repository(tmp_path)
    await repository.save_mysekai_latest_capture(
        qq=QQ,
        game_user_id=GAME_ID,
        upload_id="u1",
        captured_at_ms=1,
        uploaded_at_ms=1,
        map_png=b"map-u1",
        card_png=b"card-u1",
    )
    await repository.save_mysekai_latest_capture(
        qq=QQ,
        game_user_id=SECOND_GAME_ID,
        upload_id="u2",
        captured_at_ms=2,
        uploaded_at_ms=2,
        map_png=b"map-u2",
        card_png=b"card-u2",
    )

    first = await repository.get_mysekai_latest_capture(QQ, GAME_ID)
    second = await repository.get_mysekai_latest_capture(QQ, SECOND_GAME_ID)
    assert first is not None and first["map_png"] == b"map-u1"
    assert second is not None and second["map_png"] == b"map-u2"


async def test_latest_capture_endpoint_404_when_empty(tmp_path) -> None:
    async with _app(tmp_path) as (client, _payloads):
        response = await client.post(
            "/internal/v1/mysekai/latest-capture",
            json={"qq": QQ},
            headers={"Authorization": f"Bearer {BOT_TOKEN}"},
        )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "no_capture"


async def test_latest_capture_reports_not_bound(tmp_path) -> None:
    async with _client(tmp_path) as client:
        response = await client.post(
            "/internal/v1/mysekai/latest-capture",
            json={"qq": QQ},
            headers={"Authorization": f"Bearer {BOT_TOKEN}"},
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_bound"


async def test_latest_capture_requires_internal_token(tmp_path) -> None:
    async with _client(tmp_path) as client:
        response = await client.post(
            "/internal/v1/mysekai/latest-capture",
            json={"qq": QQ},
        )
    assert response.status_code == 403


async def test_submit_stores_the_latest_capture(tmp_path) -> None:
    async with _app(tmp_path) as (client, _payloads):
        await _submit(client)
        response = await client.post(
            "/internal/v1/mysekai/latest-capture",
            json={"qq": QQ},
            headers={"Authorization": f"Bearer {BOT_TOKEN}"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["uploaded_at_ms"] > 0
    assert base64.b64decode(payload["map"]) == TINY_PNG
    assert base64.b64decode(payload["card"]) == TINY_PNG
