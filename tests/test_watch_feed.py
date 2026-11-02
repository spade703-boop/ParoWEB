"""浇水看板：专属链接、事件流、权限。"""

from __future__ import annotations

import base64
import hashlib
import hmac
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlparse

import httpx

from app.main import create_app

from test_mysekai import BOT_TOKEN, GAME_ID, QQ, _settings


@asynccontextmanager
async def _app(tmp_path, *, watch: bool = True):
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
        if watch:
            await repository.set_mysekai_water_watch(QQ, True)
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
    # 打开看板后这个 QQ 切到网页，群里不再推浇水播报
    assert await repository.is_mysekai_watch_web(QQ) is True


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


def _token(qq: str) -> str:
    """直接算令牌，避免走 watch-link（那会顺手开启监听）。"""
    digest = hmac.new(b"session-secret", f"watch:{qq}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:12]).decode().rstrip("=")


async def test_feed_is_closed_with_the_watch(tmp_path) -> None:
    async with _app(tmp_path, watch=False) as (client, repository):
        qq, token = QQ, _token(QQ)

        response = await client.get(
            f"/api/v1/mysekai/watch/feed?qq={qq}&t={token}&after=0"
        )
        # 没开监听时不标记网页模式
        page = await repository.is_mysekai_watch_web(QQ)

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "watch_closed"
    assert page is False


async def test_each_qq_keeps_only_the_three_newest_images(tmp_path) -> None:
    async with _app(tmp_path) as (_client, repository):
        for index in range(5):
            await repository.add_mysekai_watch_event(
                qq=QQ,
                game_user_id=GAME_ID,
                round_key=f"r{index}",
                kind="rare",
                image_path=f"img{index}.png",
                rounds=index,
            )

        stale = await repository.prune_mysekai_watch_images(QQ, keep=3)
        events = await repository.list_mysekai_watch_events(QQ)

    assert sorted(stale) == ["img0.png", "img1.png"]
    kept = [event["image_path"] for event in events if event["image_path"]]
    assert kept == ["img2.png", "img3.png", "img4.png"]
    # 事件本身都还在，只是旧图被清空
    assert len(events) == 5


async def test_watch_link_enables_the_watch_when_it_is_off(tmp_path, monkeypatch) -> None:
    """群友不用先发「开启监听」，直接取看板就顺手开启。"""
    from app.services.mysekai import MysekaiService

    async def open_watch(self, qq, enabled):
        assert enabled is True
        return {
            "enabled": True,
            "changed": True,
            "birthday": {"active": True, "character_name": "东云彰人"},
        }

    monkeypatch.setattr(MysekaiService, "set_water_watch", open_watch)
    async with _app(tmp_path, watch=False) as (client, _repo):
        response = await client.post(
            "/internal/v1/mysekai/watch-link",
            json={"qq": QQ},
            headers={"Authorization": f"Bearer {BOT_TOKEN}"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["changed"] is True
    assert "/roomlist/watch?qq=" in payload["url"]


async def test_watch_link_reports_a_closed_window(tmp_path, monkeypatch) -> None:
    from app.services.mysekai import MysekaiError, MysekaiService

    async def open_watch(self, qq, enabled):
        raise MysekaiError("现在还不能开启烤森监听", code="watch_window_closed", status=409)

    monkeypatch.setattr(MysekaiService, "set_water_watch", open_watch)
    async with _app(tmp_path, watch=False) as (client, _repo):
        response = await client.post(
            "/internal/v1/mysekai/watch-link",
            json={"qq": QQ},
            headers={"Authorization": f"Bearer {BOT_TOKEN}"},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "watch_window_closed"
