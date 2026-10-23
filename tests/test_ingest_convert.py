from __future__ import annotations

import httpx

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
