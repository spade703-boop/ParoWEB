from __future__ import annotations

import asyncio

import httpx
import pytest

from test_mysekai import BOT_TOKEN, GAME_ID, QQ
from test_push_dedupe import _app

SLOT = {
    "slotId": 2,
    "mysekaiConvertItemId": 22,
    "convertStartAt": 1792723215029,
    "onceConvertMinutes": 60,
    "convertCount": 8,
    "receivedProgress": 0,
    "mysekaiConvertSlotStatus": "processing",
    "userMysekaiConvertObtainResources": [{"seq": 0}],
}


async def _ingest_convert(client: httpx.AsyncClient) -> httpx.Response:
    return await client.post(
        "/api/v1/mysekai/ingest-convert",
        content=b"cipher-bytes",
        headers={
            "Authorization": "Bearer ingest-token",
            "Content-Type": "application/octet-stream",
            "X-Mysekai-Game-Id": GAME_ID,
        },
    )


async def test_convert_action_replaces_stored_slots(tmp_path) -> None:
    async with _app(
        tmp_path, convert_state={"present": True, "slots": [SLOT]}
    ) as (client, _payloads):
        repository = client._transport.app.state.services.repository  # type: ignore[attr-defined]
        await repository.set_mysekai_catalog_state(
            server="cn",
            game_user_id=GAME_ID,
            music_records=("keep-me",),
            blueprints=None,
            fixtures=None,
            convert_slots=[{**SLOT, "slotId": 9}],
            convert_histories=None,
            mysekai_rank=12,
            refreshed_at_ms=1792723215029,
        )

        response = await _ingest_convert(client)

        state = await repository.get_mysekai_catalog_state("cn", GAME_ID)

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "updated": True, "slots": 1}
    assert [slot["slotId"] for slot in state["convert_slots"]] == [2]
    # 其它列必须原样保留
    assert state["music_records"] == ["keep-me"]
    assert state["mysekai_rank"] == 12


async def test_absent_convert_state_keeps_stored_slots(tmp_path) -> None:
    async with _app(tmp_path, convert_state={"present": False}) as (client, _payloads):
        repository = client._transport.app.state.services.repository  # type: ignore[attr-defined]
        await repository.set_mysekai_catalog_state(
            server="cn",
            game_user_id=GAME_ID,
            music_records=None,
            blueprints=None,
            fixtures=None,
            convert_slots=[SLOT],
            convert_histories=None,
            mysekai_rank=None,
            refreshed_at_ms=None,
        )

        response = await _ingest_convert(client)

        state = await repository.get_mysekai_catalog_state("cn", GAME_ID)

    assert response.json() == {"ok": True, "updated": False}
    assert [slot["slotId"] for slot in state["convert_slots"]] == [2]


async def test_convert_action_preserves_concurrent_full_upload(tmp_path, monkeypatch) -> None:
    async with _app(
        tmp_path, convert_state={"present": True, "slots": [SLOT]}
    ) as (client, _payloads):
        repository = client._transport.app.state.services.repository
        save_state = repository.set_mysekai_catalog_state
        await save_state(
            server="cn", game_user_id=GAME_ID,
            music_records=[1], blueprints=[1], fixtures=[1],
            convert_histories=[1], mysekai_rank=1, refreshed_at_ms=100,
        )
        saving = asyncio.Event()
        resume = asyncio.Event()

        async def delayed_save(**fields):
            saving.set()
            await asyncio.wait_for(resume.wait(), timeout=5)
            await save_state(**fields)

        monkeypatch.setattr(repository, "set_mysekai_catalog_state", delayed_save)
        request = asyncio.create_task(_ingest_convert(client))
        try:
            await asyncio.wait_for(saving.wait(), timeout=5)
            await save_state(
                server="cn", game_user_id=GAME_ID,
                music_records=[1, 2], blueprints=[1, 2], fixtures=[1, 2],
                convert_histories=[1, 2], mysekai_rank=2, refreshed_at_ms=200,
            )
        finally:
            resume.set()
            response = await request
        state = await repository.get_mysekai_catalog_state("cn", GAME_ID)

    assert response.status_code == 200
    for field in ("music_records", "blueprints", "fixtures", "convert_histories"):
        assert state[field] == [1, 2]
    assert state["mysekai_rank"] == 2
    assert state["refreshed_at_ms"] == 200
    assert state["convert_slots"] == [SLOT]


async def test_present_empty_convert_state_clears_slots(tmp_path) -> None:
    async with _app(
        tmp_path, convert_state={"present": True, "slots": []}
    ) as (client, _payloads):
        repository = client._transport.app.state.services.repository
        await repository.set_mysekai_catalog_state(
            server="cn", game_user_id=GAME_ID,
            music_records=None, blueprints=None, convert_slots=[SLOT],
        )
        response = await _ingest_convert(client)
        state = await repository.get_mysekai_catalog_state("cn", GAME_ID)

    assert response.json() == {"ok": True, "updated": True, "slots": 0}
    assert state["convert_slots"] == []


async def test_convert_action_never_pushes(tmp_path) -> None:
    async with _app(
        tmp_path, convert_state={"present": True, "slots": [SLOT]}
    ) as (client, push_payloads):
        await _ingest_convert(client)

    assert push_payloads == []


async def test_convert_action_updates_the_watch_table(tmp_path) -> None:
    async with _app(
        tmp_path, convert_state={"present": True, "slots": [SLOT]}
    ) as (client, _payloads):
        repository = client._transport.app.state.services.repository  # type: ignore[attr-defined]
        await repository.set_mysekai_convert_watch(QQ, True)
        response = await _ingest_convert(client)
        async with repository.connect() as connection:
            row = await (
                await connection.execute(
                    "SELECT finish_at_ms FROM mysekai_convert_slots "
                    "WHERE game_user_id = ? AND slot_id = 2",
                    (GAME_ID,),
                )
            ).fetchone()

    assert response.status_code == 200
    assert row is not None


async def test_convert_ingest_can_be_switched_off(tmp_path) -> None:
    """运维开关：关掉后旁路直接 503，已有槽位不动。"""
    async with _app(
        tmp_path,
        convert_state={"present": True, "slots": [SLOT]},
        convert_ingest=False,
    ) as (client, _payloads):
        repository = client._transport.app.state.services.repository  # type: ignore[attr-defined]
        await repository.set_mysekai_catalog_state(
            server="cn",
            game_user_id=GAME_ID,
            music_records=None,
            blueprints=None,
            fixtures=None,
            convert_slots=[SLOT],
            convert_histories=None,
            mysekai_rank=None,
            refreshed_at_ms=None,
        )

        response = await _ingest_convert(client)
        state = await repository.get_mysekai_catalog_state("cn", GAME_ID)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "convert_ingest_disabled"
    assert [slot["slotId"] for slot in state["convert_slots"]] == [2]


async def test_old_batch_ack_cannot_mark_the_replacement_as_notified(tmp_path) -> None:
    async with _app(tmp_path, convert_state={"present": True, "slots": [SLOT]}) as (
        client,
        _payloads,
    ):
        repository = client._transport.app.state.services.repository
        await repository.set_mysekai_convert_watch(QQ, True)
        await repository.record_mysekai_convert_slots(GAME_ID, [SLOT])
        due = await client.get(
            "/internal/v1/mysekai/convert-due",
            headers={"Authorization": f"Bearer {BOT_TOKEN}"},
        )
        old = due.json()["slots"][0]
        await repository.record_mysekai_convert_slots(
            GAME_ID,
            [{**SLOT, "convertStartAt": SLOT["convertStartAt"] + 86_400_000}],
        )

        response = await client.post(
            "/internal/v1/mysekai/convert-ack",
            json={
                "game_user_id": GAME_ID,
                "batches": [{
                    "batch_id": old["batch_id"],
                    "server": old["server"],
                    "slot_id": old["slot_id"],
                    "convert_start_at_ms": old["convert_start_at_ms"],
                }],
            },
            headers={"Authorization": f"Bearer {BOT_TOKEN}"},
        )
        legacy = await client.post(
            "/internal/v1/mysekai/convert-ack",
            json={"game_user_id": GAME_ID, "slot_ids": [2]},
            headers={"Authorization": f"Bearer {BOT_TOKEN}"},
        )
        async with repository.connect() as connection:
            row = await (
                await connection.execute(
                    "SELECT notified FROM mysekai_convert_slots "
                    "WHERE game_user_id = ? AND slot_id = 2",
                    (GAME_ID,),
                )
            ).fetchone()

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "acked": [],
        "expired": [old["batch_id"]],
    }
    assert legacy.status_code == 422
    assert row["notified"] == 0


@pytest.mark.parametrize("incoming_version", [100, 200])
async def test_rejected_convert_does_not_change_catalog_or_reminders(
    tmp_path, incoming_version,
) -> None:
    payload = {
        "present": True,
        "slots": [{**SLOT, "convertCount": 6}],
        "source_meta": {"source_observed_at_ms": 200},
    }
    async with _app(tmp_path, convert_state=payload) as (client, _payloads):
        repository = client._transport.app.state.services.repository
        await repository.set_mysekai_convert_watch(QQ, True)
        first = await _ingest_convert(client)
        assert first.json()["updated"] is True
        payload["slots"] = [SLOT]
        payload["source_meta"]["source_observed_at_ms"] = incoming_version

        rejected = await _ingest_convert(client)

        state = await repository.get_mysekai_catalog_state("cn", GAME_ID)
        async with repository.connect() as connection:
            row = await (
                await connection.execute(
                    "SELECT finish_at_ms FROM mysekai_convert_slots WHERE game_user_id = ?",
                    (GAME_ID,),
                )
            ).fetchone()
    assert rejected.json()["updated"] is False
    assert state["convert_slots"][0]["convertCount"] == 6
    assert row["finish_at_ms"] == SLOT["convertStartAt"] + 6 * 3_600_000
