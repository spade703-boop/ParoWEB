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
