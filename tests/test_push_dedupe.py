from __future__ import annotations

import base64
from contextlib import asynccontextmanager

import httpx

from app.main import create_app

from test_mysekai import (
    GAME_ID,
    QQ,
    _bind,
    _login,
    _mock_transport,
    _settings,
)


def _submit_body() -> dict:
    return {
        "qq": QQ,
        "game_user_id": GAME_ID,
        "region": "cn",
        "file_base64": base64.b64encode(b"capture").decode(),
    }


async def _submit(client: httpx.AsyncClient) -> dict:
    response = await client.post(
        "/api/v1/mysekai/submit",
        json=_submit_body(),
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 200, response.text
    return response.json()


@asynccontextmanager
async def _app(
    tmp_path,
    *,
    push_ok: bool = True,
    submit_birthday: dict | None = None,
    watch_on: bool = False,
    bind: bool = True,
    convert_state: dict | None = None,
    dedupe: bool = True,
    convert_ingest: bool = True,
):
    """一个开着推送的应用；yield (client, push_payloads)。"""
    push_payloads: list[dict] = []
    settings = _settings(
        tmp_path,
        mysekai_push_url="http://push.test/push",
        mysekai_ingest_token="ingest-token",
        mysekai_push_dedupe=dedupe,
        mysekai_convert_ingest=convert_ingest,
    )
    app = create_app(settings)
    app.state.services.mysekai.transport = _mock_transport(
        push_ok=push_ok,
        push_payloads=push_payloads,
        submit_birthday=submit_birthday,
        convert_state=convert_state,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", timeout=30
        ) as client:
            await _login(client)
            if bind:
                await _bind(client)
            if watch_on:
                await app.state.services.repository.set_mysekai_water_watch(
                    QQ, True
                )
            yield client, push_payloads


async def test_failed_rare_push_can_retry_same_round(tmp_path, monkeypatch) -> None:
    from app.services.mysekai import MysekaiService

    attempts = []

    async def render_rare(self, body):
        return b"test-png"

    async def push_rare(self, **payload):
        attempts.append(payload["upload_id"])
        return ("failed", "test failure") if len(attempts) == 1 else ("sent", "ok")

    monkeypatch.setattr(MysekaiService, "_render_rare", render_rare)
    monkeypatch.setattr(MysekaiService, "_push_rare", push_rare)
    birthday = {"targets": [{"material_id": 12}], "round_key": "retry-round"}
    async with _app(tmp_path, submit_birthday=birthday, watch_on=True) as (client, _):
        first = await _submit(client)
        second = await _submit(client)
    assert first["watch"]["status"] == "failed"
    assert second["watch"]["status"] == "sent"
    assert len(attempts) == 2
    assert attempts[0] == attempts[1]


async def test_web_submit_reports_watch_state(tmp_path) -> None:
    birthday = {"party_id": 2, "round_key": "party:2:progress:8", "targets": []}
    async with _app(tmp_path, submit_birthday=birthday, watch_on=True) as (client, _):
        response = await _submit(client)
    assert response["watch"] == {
        "status": "recorded",
        "message": "本轮已记录，未发现稀有材料",
    }


async def test_web_submit_without_watch_hides_watch_state(tmp_path) -> None:
    birthday = {"party_id": 2, "round_key": "party:2:progress:8", "targets": []}
    async with _app(tmp_path, submit_birthday=birthday, watch_on=False) as (client, _):
        response = await _submit(client)
    assert response["watch"]["status"] == "skipped"


async def test_manual_upload_labels_result_as_current_snapshot(tmp_path, monkeypatch) -> None:
    from app.services.mysekai import MysekaiService
    notifications = []

    async def push_rare(self, **payload):
        notifications.append(payload)
        return "sent", "ok"

    async def render_rare(self, body):
        return b"rare-png"

    monkeypatch.setattr(MysekaiService, "_push_rare", push_rare)
    monkeypatch.setattr(MysekaiService, "_render_rare", render_rare)
    birthday = {
        "party_id": 2, "round_key": "party:2:progress:8",
        "targets": [{"material_id": 12, "name": "钻石", "quantity": 1, "site_id": 5, "x": 1, "z": 2}],
    }
    async with _app(tmp_path, submit_birthday=birthday, watch_on=True) as (client, _):
        await _submit(client)
    assert notifications[0]["birthday"]["observation_kind"] == "snapshot"


async def test_empty_delivery_is_recorded_without_group_notification(tmp_path, monkeypatch) -> None:
    from app.services.mysekai import MysekaiService
    notifications = []

    async def push_rare(self, **payload):
        notifications.append(payload)
        return "sent", "ok"

    async def render_rare(self, body):
        raise AssertionError("empty result must not render")

    monkeypatch.setattr(MysekaiService, "_push_rare", push_rare)
    monkeypatch.setattr(MysekaiService, "_render_rare", render_rare)
    birthday = {"party_id": 2, "round_key": "party:2:progress:8", "targets": []}
    async with _app(tmp_path, submit_birthday=birthday, watch_on=True) as (client, ordinary_pushes):
        statuses = []
        for _ in range(2):
            response = await client.post(
                "/api/v1/mysekai/ingest", content=b"capture",
                headers={"Authorization": "Bearer ingest-token", "X-Mysekai-Game-Id": GAME_ID,
                         "X-Mysekai-Event": "birthday_delivery", "X-Mysekai-Party-Id": "2"},
            )
            assert response.status_code == 200
            statuses.append(response.json()["watch"]["status"])
        repository = client._transport.app.state.services.repository
        stats = await repository.mysekai_water_round_stats(GAME_ID)
        enabled_at = await repository.mysekai_water_watch_enabled_at(QQ)
        since_stats = await repository.mysekai_water_round_stats(GAME_ID, since=enabled_at)
    assert statuses == ["recorded", "duplicate"]
    assert notifications == []
    assert ordinary_pushes == []
    assert stats["rounds"] == 1
    assert stats["matched_rounds"] == 0
    assert since_stats == stats



async def test_older_progress_cannot_claim_after_newer_round(tmp_path) -> None:
    async with _app(tmp_path) as (client, _):
        repository = client._transport.app.state.services.repository
        assert await repository.claim_mysekai_water_round(GAME_ID, "party:2:progress:9")
        assert not await repository.claim_mysekai_water_round(GAME_ID, "party:2:progress:8")
        assert await repository.claim_mysekai_water_round(GAME_ID, "party:3:progress:1")


async def test_missing_maps_is_reported_as_unknown_not_empty_round(tmp_path) -> None:
    async with _app(tmp_path, watch_on=True) as (client, payloads):
        service = client._transport.app.state.services.mysekai
        original = service.transport

        def handler(request):
            if request.url.path == "/submit":
                return httpx.Response(422, json={"error": "no_harvest_maps", "message": "本次未携带采集图"})
            return original.handle_request(request)

        service.transport = httpx.MockTransport(handler)
        response = await client.post(
            "/api/v1/mysekai/submit", json=_submit_body(),
            headers={"Origin": "http://testserver"},
        )
    assert response.status_code == 422
    assert "capture_missing_maps" in response.text
    assert payloads == []


async def test_ingest_rejects_invalid_event_before_processing(tmp_path) -> None:
    async with _app(tmp_path) as (client, payloads):
        response = await client.post(
            "/api/v1/mysekai/ingest", content=b"capture",
            headers={"Authorization": "Bearer ingest-token",
                     "X-Mysekai-Game-Id": GAME_ID,
                     "X-Mysekai-Event": "unknown"},
        )
    assert response.status_code == 422
    assert payloads == []


async def test_delivery_party_mismatch_is_rejected(tmp_path) -> None:
    birthday = {"party_id": 2, "round_key": "party:2:progress:8", "targets": []}
    async with _app(tmp_path, submit_birthday=birthday) as (client, payloads):
        response = await client.post(
            "/api/v1/mysekai/ingest", content=b"capture",
            headers={"Authorization": "Bearer ingest-token",
                     "X-Mysekai-Game-Id": GAME_ID,
                     "X-Mysekai-Event": "birthday_delivery",
                     "X-Mysekai-Party-Id": "3"},
        )
    assert response.status_code == 422
    assert payloads == []


async def test_second_upload_in_same_window_is_skipped(tmp_path) -> None:
    async with _app(tmp_path) as (client, push_payloads):
        first = await _submit(client)
        second = await _submit(client)

    assert first["push"]["status"] == "sent"
    assert second["push"]["status"] == "skipped"
    assert second["push"]["message"] == "本时段已推送过，本次仅更新数据"
    assert len(push_payloads) == 1


async def test_push_failure_releases_the_window(tmp_path) -> None:
    async with _app(tmp_path, push_ok=False) as (client, _payloads):
        first = await _submit(client)
    assert first["push"]["status"] == "failed"

    async with _app(tmp_path, bind=False) as (client, retry_payloads):
        second = await _submit(client)

    assert second["push"]["status"] == "sent"
    assert len(retry_payloads) == 1


async def test_watched_user_in_birthday_window_bypasses_dedupe(tmp_path) -> None:
    birthday = {"targets": [], "round_key": "t1792723215029"}
    async with _app(
        tmp_path, submit_birthday=birthday, watch_on=True
    ) as (client, push_payloads):
        await _submit(client)
        second = await _submit(client)

    assert second["push"]["status"] == "sent"
    assert len(push_payloads) == 2


async def test_unwatched_user_in_birthday_window_is_still_deduped(tmp_path) -> None:
    """豁免只属于开了浇水监听的人，不能变成「生日当天全员放开」。"""
    birthday = {"targets": [], "round_key": "t1792723215029"}
    async with _app(
        tmp_path, submit_birthday=birthday, watch_on=False
    ) as (client, push_payloads):
        await _submit(client)
        second = await _submit(client)

    assert second["push"]["status"] == "skipped"
    assert len(push_payloads) == 1


async def test_unwatched_user_does_not_send_the_birthday_line(tmp_path) -> None:
    """生日材料提示只属于开了浇水监听的人；普通上传不能附带。"""
    birthday = {
        "targets": [
            {
                "material_id": 12,
                "name": "钻石",
                "quantity": 1,
                "site_id": 5,
                "x": 1,
                "z": 2,
            }
        ],
        "round_key": "t1792723215029",
    }
    async with _app(
        tmp_path, submit_birthday=birthday, watch_on=False
    ) as (client, push_payloads):
        await _submit(client)

    assert len(push_payloads) == 1
    assert push_payloads[0]["birthday"] is None


async def test_main_map_never_duplicates_the_watering_notification(tmp_path) -> None:
    """生日结果只走独立通知，不附在普通地图推送里。"""
    birthday = {
        "targets": [
            {
                "material_id": 12,
                "name": "钻石",
                "quantity": 1,
                "site_id": 5,
                "x": 1,
                "z": 2,
            }
        ],
        "round_key": "t1792723215029",
    }
    async with _app(
        tmp_path, submit_birthday=birthday, watch_on=True
    ) as (client, push_payloads):
        await _submit(client)

    assert push_payloads[0]["birthday"] is None


async def test_dedupe_can_be_switched_off(tmp_path) -> None:
    """运维开关：关掉后回到「每次上传都推」。"""
    async with _app(tmp_path, dedupe=False) as (client, push_payloads):
        await _submit(client)
        second = await _submit(client)

    assert second["push"]["status"] == "sent"
    assert len(push_payloads) == 2


async def test_a_broken_dedupe_store_still_pushes(tmp_path) -> None:
    """去重表出问题时必须失败即放行，不能让上传挂掉。"""
    async with _app(tmp_path) as (client, push_payloads):
        repository = client._transport.app.state.services.repository  # type: ignore[attr-defined]

        async def boom(*_args, **_kwargs):
            raise RuntimeError("database is locked")

        repository.claim_mysekai_push_window = boom
        response = await _submit(client)

    assert response["push"]["status"] == "sent"
    assert len(push_payloads) == 1


async def test_a_broken_capture_store_does_not_block_the_upload(tmp_path) -> None:
    """最近抓包只是指令用的缓存，写失败不该影响上传与推送。"""
    async with _app(tmp_path) as (client, push_payloads):
        repository = client._transport.app.state.services.repository  # type: ignore[attr-defined]

        async def boom(*_args, **_kwargs):
            raise RuntimeError("disk full")

        repository.save_mysekai_latest_capture = boom
        response = await _submit(client)

    assert response["push"]["status"] == "sent"
    assert len(push_payloads) == 1
