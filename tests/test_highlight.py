"""Per-QQ extra highlight materials: persistence, name resolution and header."""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock

import pytest

from app.repositories.sqlite import SQLiteRepository
from app.services.mysekai import (
    HIGHLIGHT_LIMIT,
    MysekaiError,
    MysekaiService,
)

from test_mysekai import _mock_transport, _settings

QQ = "123456789"
MATERIALS = [
    {"id": 8, "name": "铁矿石", "rarity": "rarity_1"},
    {"id": 12, "name": "钻石", "rarity": "rarity_3"},
    {"id": 34, "name": "蓬松的棉花", "rarity": "rarity_4"},
]


async def _service(tmp_path) -> MysekaiService:
    settings = _settings(tmp_path)
    repository = SQLiteRepository(settings.database_path)
    await repository.initialize()
    service = MysekaiService(settings, repository, transport=_mock_transport())
    service.fetch_materials = AsyncMock(return_value=list(MATERIALS))
    return service


async def test_set_replaces_the_list_and_status_shows_names(tmp_path) -> None:
    service = await _service(tmp_path)

    first = await service.set_highlights(QQ, ["铁矿石", "钻石"])
    second = await service.set_highlights(QQ, ["钻石"])
    status = await service.highlight_status(QQ)

    assert [row["id"] for row in first["materials"]] == [8, 12]
    assert second["count"] == 1
    assert [row["name"] for row in status["materials"]] == ["钻石"]
    assert status["count"] == 1
    assert status["limit"] == HIGHLIGHT_LIMIT


async def test_remove_and_clear(tmp_path) -> None:
    service = await _service(tmp_path)
    await service.set_highlights(QQ, ["铁矿石", "钻石"])

    removed = await service.remove_highlights(QQ, ["铁矿石"])
    cleared = await service.clear_highlights(QQ)

    assert removed["removed"] == 1
    assert [row["id"] for row in removed["materials"]] == [12]
    assert cleared["removed"] == 1
    assert (await service.highlight_status(QQ))["count"] == 0


async def test_unknown_name_is_rejected_atomically(tmp_path) -> None:
    service = await _service(tmp_path)

    with pytest.raises(MysekaiError) as raised:
        await service.set_highlights(QQ, ["不存在"])

    assert raised.value.code == "highlight_name_invalid"
    assert (await service.highlight_status(QQ))["count"] == 0


async def test_too_many_highlights_are_rejected(tmp_path) -> None:
    service = await _service(tmp_path)
    many = [
        {"id": i, "name": f"材料{i}", "rarity": "rarity_1"}
        for i in range(1, HIGHLIGHT_LIMIT + 2)
    ]
    service.fetch_materials = AsyncMock(return_value=many)

    with pytest.raises(MysekaiError) as raised:
        await service.set_highlights(QQ, [row["name"] for row in many])

    assert raised.value.code == "highlight_too_many"


async def test_render_sends_the_highlight_header(tmp_path) -> None:
    captured: list[dict] = []
    settings = _settings(tmp_path)
    repository = SQLiteRepository(settings.database_path)
    await repository.initialize()
    service = MysekaiService(
        settings, repository, transport=_mock_transport(submit_headers=captured)
    )

    await service._render(b"x", {"nickname": "n"}, None, frozenset({8, 12}))

    header = captured[0]["x-mysekai-highlight"]
    decoded = base64.urlsafe_b64decode(header + "=" * (-len(header) % 4))
    assert json.loads(decoded) == [8, 12]


async def test_material_catalog_image_is_fetched_and_cached(tmp_path) -> None:
    import httpx

    from test_mysekai import TINY_PNG

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, content=TINY_PNG, headers={"content-type": "image/png"})

    settings = _settings(tmp_path)
    repository = SQLiteRepository(settings.database_path)
    await repository.initialize()
    service = MysekaiService(
        settings, repository, transport=httpx.MockTransport(handler)
    )

    first = await service.render_material_catalog()
    second = await service.render_material_catalog()

    assert first == second == TINY_PNG
    assert calls == ["/render/materials"]
