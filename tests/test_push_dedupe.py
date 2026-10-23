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
):
    """一个开着推送的应用；yield (client, push_payloads)。"""
    push_payloads: list[dict] = []
    settings = _settings(tmp_path, mysekai_push_url="http://push.test/push")
    app = create_app(settings)
    app.state.services.mysekai.transport = _mock_transport(
        push_ok=push_ok,
        push_payloads=push_payloads,
        submit_birthday=submit_birthday,
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
