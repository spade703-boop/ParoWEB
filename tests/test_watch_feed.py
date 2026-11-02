"""浇水看板：专属链接、事件流、权限。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlparse

import httpx

from app.main import create_app

from test_mysekai import BOT_TOKEN, GAME_ID, QQ, _settings


@asynccontextmanager
async def _app(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        repository = app.state.services.repository
        await repository.create_mysekai_binding(
            qq=QQ,
            server="cn",
            game_user_id=GAME_ID,
            nickname="测试玩家",
            deck_id=None,
            leader_card_id=None,
            leader_avatar=None,
            profile_image_type=None,
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            yield client, repository


async def _link(client) -> tuple[str, str]:
    response = await client.post(
        "/internal/v1/mysekai/watch-link",
        json={"qq": QQ},
        headers={"Authorization": f"Bearer {BOT_TOKEN}"},
    )
    assert response.status_code == 200, response.text
    url = response.json()["url"]
    query = parse_qs(urlparse(url).query)
    return query["qq"][0], query["t"][0]


async def test_watch_link_is_per_qq_and_needs_the_internal_token(tmp_path) -> None:
    async with _app(tmp_path) as (client, _repo):
        qq, token = await _link(client)
        assert qq == QQ and token

        rejected = await client.post(
            "/internal/v1/mysekai/watch-link", json={"qq": QQ}
        )
    assert rejected.status_code == 403


async def test_feed_returns_only_events_for_a_valid_token(tmp_path) -> None:
    async with _app(tmp_path) as (client, repository):
        qq, token = await _link(client)
        await repository.add_mysekai_watch_event(
            qq=QQ,
            game_user_id=GAME_ID,
            round_key="party:2:progress:8",
            kind="empty",
            rounds=3,
        )

        ok = await client.get(
            f"/api/v1/mysekai/watch/feed?qq={qq}&t={token}&after=0"
        )
        bad = await client.get(
            f"/api/v1/mysekai/watch/feed?qq={qq}&t=wrong"
        )

    assert ok.status_code == 200
    payload = ok.json()
    assert len(payload["events"]) == 1
    assert payload["events"][0]["kind"] == "empty"
    assert payload["events"][0]["rounds"] == 3
    assert payload["roles"][0]["game_user_id"] == GAME_ID
    assert bad.status_code == 403


async def test_feed_after_id_skips_history(tmp_path) -> None:
    async with _app(tmp_path) as (client, repository):
        qq, token = await _link(client)
        first = await repository.add_mysekai_watch_event(
            qq=QQ, game_user_id=GAME_ID, round_key="r1", kind="empty", rounds=1
        )
        second = await repository.add_mysekai_watch_event(
            qq=QQ, game_user_id=GAME_ID, round_key="r2", kind="empty", rounds=2
        )

        response = await client.get(
            f"/api/v1/mysekai/watch/feed?qq={qq}&t={token}&after={first}"
        )

    events = response.json()["events"]
    assert [event["id"] for event in events] == [second]


async def test_watch_page_is_served(tmp_path) -> None:
    async with _app(tmp_path) as (client, _repo):
        response = await client.get("/roomlist/watch")
    assert response.status_code == 200
    assert "烤森浇水看板" in response.text
