"""Delivery uploads: watch gate, no normal map push, birthday state upsert."""

from __future__ import annotations

import base64
import json
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


def _decode_header(value: str) -> list[int]:
    raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    return json.loads(raw)


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
        "message": "本轮未命中监听材料",
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


def _transport(
    push_calls: list[dict],
    submit_headers: list[dict],
    delivery_payload: dict | None = None,
) -> httpx.MockTransport:
    rendered = delivery_payload or _delivery_payload()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/api/public/"):
            return httpx.Response(500, json={"error": "haruki down"})
        if path == "/materials":
            return httpx.Response(200, json={"materials": [
                {"id": 8, "name": "铁", "rarity": "rarity_1"},
                {"id": 12, "name": "钻石", "rarity": "rarity_3"},
                {"id": 20, "name": "四叶草", "rarity": "rarity_3"},
            ]})
        if path == "/submit":
            submit_headers.append(dict(request.headers))
            if request.headers.get("X-Mysekai-Watch") != "1":
                return httpx.Response(422, json={"error": "watch_required"})
            return httpx.Response(200, json=rendered)
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
    await repository.replace_mysekai_water_watch_materials(
        QQ, frozenset({8, 12, 20})
    )

    result = await service.submit(
        qq=QQ, game_user_id=GAME, server="cn", file_base64=_body()
    )

    assert result["kind"] == "birthday_delivery"
    assert result["message"] == "本轮未命中监听材料"
    assert result["rare"] is None
    assert result["birthday"].startswith("data:image/png;base64,")
    assert push_calls == []
    assert submit_headers[0]["x-mysekai-watch"] == "1"
    assert _decode_header(
        submit_headers[0]["x-mysekai-watch-materials"]
    ) == [8, 12, 20]

    row = await repository.get_mysekai_birthday_state("cn", GAME)
    assert row is not None
    assert row["party_id"] == 2
    assert row["obtained"] == 25


async def test_one_capture_saves_each_party_independently(tmp_path: Path) -> None:
    payload = _delivery_payload()
    payload["birthdays"] = [
        payload["birthday"],
        {
            "party_id": 3,
            "character_id": 4,
            "obtained": 7,
            "dropped": 1,
            "total_point": 70000,
            "round_key": "party:3:progress:8",
            "round_source": "progress",
            "targets": [],
        },
    ]
    service, repository = await _service(
        tmp_path, _transport([], [], payload)
    )
    await repository.set_mysekai_water_watch(QQ, True)

    await service.submit(
        qq=QQ, game_user_id=GAME, server="cn", file_base64=_body()
    )

    rows = await repository.list_mysekai_birthday_states("cn", GAME)
    assert {(row["party_id"], row["obtained"]) for row in rows} == {
        (2, 25),
        (3, 7),
    }



async def test_render_push_returns_one_composed_png(tmp_path: Path) -> None:
    captured: list[bytes] = []
    captured_headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/render/push":
            captured.append(request.content)
            captured_headers.append(request.headers)
            return httpx.Response(
                200, content=b"composed", headers={"content-type": "image/png"}
            )
        return httpx.Response(404, json={"error": "not found"})

    service, _repository = await _service(tmp_path, httpx.MockTransport(handler))

    assert await service._render_push(b"body", frozenset({5})) == b"composed"
    assert captured == [b"body"]
    assert _decode_header(
        captured_headers[0]["x-mysekai-watch-materials"]
    ) == [5]
