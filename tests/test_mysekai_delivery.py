"""Delivery uploads: watch gate, no normal map push, birthday state upsert."""

from __future__ import annotations

import base64
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.repositories.sqlite import SQLiteRepository
from app.services.mysekai import MysekaiError, MysekaiService

QQ = "123456789"
GAME = "7440000000000000000"
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
        mysekai_internal_token="bot-token",
        mysekai_push_url="",
        mysekai_push_token="",
    )


def _delivery_payload() -> dict:
    return {
        "kind": "birthday_delivery",
        "message": "本轮无稀有材料",
        "targets": [],
        "birthday": {
            "party_id": 2,
            "character_id": 3,
            "obtained": 25,
            "dropped": 0,
            "total_point": 241290,
            "round_key": "party:2:progress:25",
            "round_source": "progress",
            "targets": [],
        },
        "rare_png_base64": None,
        "birthday_png_base64": base64.b64encode(TINY_PNG).decode(),
    }


def _transport(push_calls: list[dict], submit_headers: list[dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/api/public/"):
            return httpx.Response(500, json={"error": "haruki down"})
        if path == "/submit":
            submit_headers.append(dict(request.headers))
            if request.headers.get("X-Mysekai-Watch") != "1":
                return httpx.Response(422, json={"error": "watch_required"})
            return httpx.Response(200, json=_delivery_payload())
        if path == "/push":
            push_calls.append({})
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler)


async def _service(
    tmp_path: Path, transport: httpx.MockTransport
) -> tuple[MysekaiService, SQLiteRepository]:
    settings = _settings(tmp_path)
    repository = SQLiteRepository(settings.database_path)
    await repository.initialize()
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
    return MysekaiService(settings, repository, transport=transport), repository


def _body() -> str:
    return base64.b64encode(b"x" * 32).decode()


async def test_watch_required_raises_a_typed_error(tmp_path: Path) -> None:
    service, _repository = await _service(tmp_path, _transport([], []))

    with pytest.raises(MysekaiError) as exc:
        await service.submit(
            qq=QQ, game_user_id=GAME, server="cn", file_base64=_body()
        )

    assert exc.value.code == "birthday_watch_required"
    assert exc.value.status == 422


async def test_delivery_is_not_pushed_as_a_normal_map(tmp_path: Path) -> None:
    push_calls: list[dict] = []
    submit_headers: list[dict] = []
    service, repository = await _service(
        tmp_path, _transport(push_calls, submit_headers)
    )
    await repository.set_mysekai_water_watch(QQ, True)

    result = await service.submit(
        qq=QQ, game_user_id=GAME, server="cn", file_base64=_body()
    )

    assert result["kind"] == "birthday_delivery"
    assert result["message"] == "本轮无稀有材料"
    assert result["rare"] is None
    assert result["birthday"].startswith("data:image/png;base64,")
    assert push_calls == []
    assert submit_headers[0]["x-mysekai-watch"] == "1"

    row = await repository.get_mysekai_birthday_state("cn", GAME)
    assert row is not None
    assert row["party_id"] == 2
    assert row["obtained"] == 25
