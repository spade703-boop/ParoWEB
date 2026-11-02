"""查询浇水监听：顶层「最近一次检查」要覆盖全部角色。"""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest

from app.main import create_app
from app.repositories import sqlite as sqlite_module

from test_mysekai import BOT_TOKEN, GAME_ID, QQ, _settings

SECOND_ID = "7440000000000000001"


@asynccontextmanager
async def _app(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            yield client, app


async def test_latest_check_time_covers_every_role(tmp_path, monkeypatch) -> None:
    clock = {"now": "2026-11-02T00:00:00Z"}
    monkeypatch.setattr(sqlite_module, "utc_now", lambda: clock["now"])

    async with _app(tmp_path) as (client, app):
        repository = app.state.services.repository
        for game_id in (GAME_ID, SECOND_ID):
            await repository.create_mysekai_binding(
                qq=QQ,
                server="cn",
                game_user_id=game_id,
                nickname=None,
                deck_id=None,
                leader_card_id=None,
                leader_avatar=None,
                profile_image_type=None,
            )
        await repository.set_mysekai_water_watch(QQ, True)
        # 第一轮来自当前角色，第二（更晚）轮来自第二个角色。
        clock["now"] = "2026-11-02T01:00:00Z"
        await repository.claim_mysekai_water_round(GAME_ID, "party:2:progress:1")
        clock["now"] = "2026-11-02T03:00:00Z"
        await repository.claim_mysekai_water_round(SECOND_ID, "party:2:progress:2")

        response = await client.post(
            "/internal/v1/mysekai/watch-status",
            json={"qq": QQ},
            headers={"Authorization": f"Bearer {BOT_TOKEN}"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["latest_round_at"] == "2026-11-02T03:00:00Z"
    assert {row["game_user_id"] for row in payload["roles"]} == {GAME_ID, SECOND_ID}
