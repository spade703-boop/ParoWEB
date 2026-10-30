"""HTTP routes for the MySekai 群友入口.

The page talks JSON only (ParoWEB's security middleware requires it), and the
MySekai service stays on loopback: this backend is the only thing that calls it.
"""

from __future__ import annotations

import base64
import hmac
import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import quote

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from app.services.catalog_link import CatalogLinkError, issue, parse
from app.services.mysekai import (
    COOKIE_NAME,
    MAX_BODY_BYTES,
    SESSION_TTL_SECONDS,
    CATALOG_ENDPOINTS,
    MysekaiError,
    MysekaiService,
    SessionInvalid,
    format_capture_time,
    resolve_gate_unit,
)
from app.web.errors import error_response


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/mysekai")
internal_router = APIRouter(prefix="/internal/v1/mysekai")

QQ_PATTERN = r"^\d{5,12}$"
GAME_ID_PATTERN = r"^\d{15,25}$"
QQ_RE = re.compile(QQ_PATTERN)
GAME_ID_RE = re.compile(GAME_ID_PATTERN)


class AuthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=128)


class BindRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qq: str = Field(pattern=QQ_PATTERN)
    game_user_id: str = Field(pattern=GAME_ID_PATTERN)
    server: str = Field(default="cn", pattern=r"^(cn|jp|tw|kr|en)$")


class WatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qq: str = Field(pattern=QQ_PATTERN)
    enabled: bool


class WatchQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qq: str = Field(pattern=QQ_PATTERN)


class ConvertAckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_user_id: str = Field(pattern=GAME_ID_PATTERN)
    slot_ids: list[int] = Field(min_length=1, max_length=8)


class ConvertDoneRequest(BaseModel):
    """One completion notice: which finished slots to draw."""

    model_config = ConfigDict(extra="forbid")

    game_user_id: str = Field(pattern=GAME_ID_PATTERN)
    slot_ids: list[int] = Field(min_length=1, max_length=8)


class GateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_user_id: str = Field(pattern=GAME_ID_PATTERN)
    server: str = Field(default="cn", pattern=r"^(cn|jp|tw|kr|en)$")
    #: Team token typed after the command; empty keeps the default gate choice.
    unit: str | None = Field(default=None, max_length=16)


class CatalogRequest(BaseModel):
    """Common body for the three catalog commands."""

    model_config = ConfigDict(extra="forbid")

    game_user_id: str = Field(pattern=GAME_ID_PATTERN)
    server: str = Field(default="cn", pattern=r"^(cn|jp|tw|kr|en)$")


class TalkListRequest(CatalogRequest):
    """The raw text typed after the command; the render service owns the grammar."""

    query: str = Field(min_length=1, max_length=64)


class MusicRecordRequest(CatalogRequest):
    show_id: bool = False


class CatalogLinkRequest(CatalogRequest):
    """Which card the link should open, plus the typed query for talk lists."""

    kind: str = Field(pattern=r"^(fixture-list|talk-list|music-record)$")
    query: str = Field(default="", max_length=64)


class MasterUpdateRequest(BaseModel):
    """Which region's master snapshot to refresh."""

    model_config = ConfigDict(extra="forbid")

    region: str = Field(default="sc", pattern=r"^(sc|jp|tc)$")


class SubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qq: str = Field(pattern=QQ_PATTERN)
    game_user_id: str = Field(pattern=GAME_ID_PATTERN)
    region: str = Field(default="cn", pattern=r"^(cn|jp|tw|kr|en)$")
    file_base64: str = Field(min_length=1, max_length=6_000_000)


class QqRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qq: str = Field(pattern=QQ_PATTERN)


class UnbindRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qq: str = Field(pattern=QQ_PATTERN)
    #: Unbind one role; omitted means the QQ's current role.
    game_user_id: str | None = Field(default=None, pattern=GAME_ID_PATTERN)


class SwitchRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qq: str = Field(pattern=QQ_PATTERN)
    game_user_id: str = Field(pattern=GAME_ID_PATTERN)


class BirthdayProgressRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qq: str = Field(pattern=QQ_PATTERN)
    #: Omitted means the QQ's current role.
    game_user_id: str | None = Field(default=None, pattern=GAME_ID_PATTERN)


class LatestCaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qq: str = Field(pattern=QQ_PATTERN)
    #: Omitted means the QQ's current role.
    game_user_id: str | None = Field(default=None, pattern=GAME_ID_PATTERN)


class HighlightNamesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qq: str = Field(pattern=QQ_PATTERN)
    names: list[str] = Field(default_factory=list)
    #: False = merge into the current list (设置); True = replace it (替换).
    replace: bool = True


def _mysekai(request: Request) -> MysekaiService:
    return request.app.state.services.mysekai


def _require_session(request: Request) -> None:
    if not _mysekai(request).verify_token(request.cookies.get(COOKIE_NAME)):
        raise SessionInvalid("登录已过期，请重新输入密码")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _internal_auth_error(request: Request):
    expected = request.app.state.settings.mysekai_internal_token
    if not expected:
        return error_response(503, "internal_api_disabled", "MySekai 内部接口未配置")
    scheme, _, supplied = request.headers.get("Authorization", "").partition(" ")
    valid = scheme.lower() == "bearer" and hmac.compare_digest(
        supplied.encode("utf-8"), expected.encode("utf-8")
    )
    if not valid:
        return error_response(403, "forbidden", "无权调用 MySekai 内部接口")
    return None


def _serialize_binding(binding: dict, *, with_avatar: bool = True) -> dict:
    avatar = binding.get("leader_avatar")
    return {
        "qq": binding["qq"],
        "server": binding["server"],
        "game_user_id": binding["game_user_id"],
        "nickname": binding.get("nickname"),
        "deck_id": binding.get("deck_id"),
        "leader_card_id": binding.get("leader_card_id"),
        "avatar_data_url": (
            f"data:image/png;base64,{avatar}" if with_avatar and avatar else None
        ),
    }


@router.post("/auth")
async def authenticate(
    payload: AuthRequest, request: Request, response: Response
):
    service = _mysekai(request)
    limiter = request.app.state.services.mysekai_auth_limiter
    allowed, retry_after = limiter.check(_client_ip(request))
    if not allowed:
        return error_response(
            429,
            "rate_limited",
            "尝试次数过多，请稍后再试",
            headers={"Retry-After": str(retry_after)},
        )
    try:
        service.verify_password(payload.password)
    except MysekaiError as exc:
        return error_response(exc.status, exc.code, str(exc))
    token = service.issue_token()
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=request.app.state.settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return {"ok": True, "expires_in": SESSION_TTL_SECONDS}


@router.get("/session")
async def session(request: Request) -> dict:
    service = _mysekai(request)
    return {
        "enabled": service.enabled(),
        "authenticated": service.verify_token(request.cookies.get(COOKIE_NAME)),
    }


@router.delete("/session")
async def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@internal_router.post("/bind")
async def bind(payload: BindRequest, request: Request):
    auth_error = _internal_auth_error(request)
    if auth_error is not None:
        return auth_error
    if payload.server != "cn":
        return error_response(422, "unsupported_server", "第一版只支持国服")
    try:
        binding = await _mysekai(request).bind(
            payload.qq, payload.game_user_id, payload.server
        )
    except MysekaiError as exc:
        return error_response(exc.status, exc.code, str(exc), exc.details)
    return {"ok": True, "binding": _serialize_binding(binding)}


@internal_router.post("/watch")
async def water_watch(payload: WatchRequest, request: Request):
    auth_error = _internal_auth_error(request)
    if auth_error is not None:
        return auth_error
    service = _mysekai(request)
    try:
        result = await service.set_water_watch(payload.qq, payload.enabled)
    except MysekaiError as exc:
        return error_response(exc.status, exc.code, str(exc), exc.details)
    return {"ok": True, **result}


@internal_router.post("/watch-expire")
async def water_watch_expire(request: Request):
    """Close every watch once the observed birthday window has ended."""
    auth_error = _internal_auth_error(request)
    if auth_error is not None:
        return auth_error
    service = _mysekai(request)
    return {"ok": True, **await service.expire_water_watches()}


@internal_router.post("/watch-status")
async def water_watch_status(payload: WatchQueryRequest, request: Request):
    """How many watering rounds this account has seen, and the latest time."""
    auth_error = _internal_auth_error(request)
    if auth_error is not None:
        return auth_error
    service = _mysekai(request)
    binding = await service.repository.get_mysekai_binding(payload.qq)
    if binding is None:
        return {
            "ok": True,
            "enabled": False,
            "bound": False,
            "message": "这个 QQ 还没有绑定游戏 ID",
        }
    enabled = await service.repository.is_mysekai_water_watch(payload.qq)
    enabled_at = await service.repository.mysekai_water_watch_enabled_at(payload.qq)
    stats = await service.repository.mysekai_water_round_stats(
        binding["game_user_id"], since=enabled_at
    )
    # 监听仍是 QQ 一条开关，但轮次本来就是按角色分开记的：一并给出 uN 明细，
    # 群里才能看出是哪个角色浇的。
    bindings = await service.repository.list_mysekai_bindings(payload.qq)
    roles = []
    for index, row in enumerate(bindings, 1):
        roles.append(
            {
                "label": f"u{index}",
                "game_user_id": row["game_user_id"],
                **(await service.repository.mysekai_water_round_stats(
                    row["game_user_id"], since=enabled_at
                )),
            }
        )
    return {
        "ok": True,
        "bound": True,
        "enabled": enabled,
        "enabled_at": enabled_at,
        "game_user_id": binding["game_user_id"],
        "roles": roles,
        **stats,
    }


@internal_router.post("/convert-watch")
async def convert_watch(payload: WatchRequest, request: Request):
    """订阅/取消订阅转换机完成提醒。"""
    auth_error = _internal_auth_error(request)
    if auth_error is not None:
        return auth_error
    service = _mysekai(request)
    try:
        await service.set_convert_watch(payload.qq, payload.enabled)
    except MysekaiError as exc:
        return error_response(exc.status, exc.code, str(exc), exc.details)
    return {"ok": True, "enabled": payload.enabled}


@internal_router.get("/convert-due")
async def convert_due(request: Request):
    """Bot 轮询：已走完、尚未通知、且主人仍订阅的槽位。"""
    auth_error = _internal_auth_error(request)
    if auth_error is not None:
        return auth_error
    service = _mysekai(request)
    now_ms = int(time.time() * 1000)
    slots = await service.repository.due_mysekai_convert_slots(now_ms)
    return {"ok": True, "now_ms": now_ms, "slots": slots}


@internal_router.post("/latest-capture")
async def latest_capture(payload: LatestCaptureRequest, request: Request):
    """Bot 指令：取某个角色最近一次抓包的地图与结算卡。"""
    auth_error = _internal_auth_error(request)
    if auth_error is not None:
        return auth_error
    service = _mysekai(request)
    try:
        row = await service.latest_capture(payload.qq, payload.game_user_id)
    except MysekaiError as exc:
        return error_response(exc.status, exc.code, str(exc), exc.details)
    return {
        "ok": True,
        "game_user_id": row["game_user_id"],
        "role_label": row["role_label"],
        "captured_at_ms": row["captured_at_ms"],
        "uploaded_at_ms": row["uploaded_at_ms"],
        "map": base64.b64encode(row["map_png"]).decode("ascii"),
        "card": base64.b64encode(row["card_png"]).decode("ascii"),
    }


@internal_router.post("/material-catalog")
async def material_catalog(request: Request):
    """The material icon + official-name sheet for the group."""
    auth_error = _internal_auth_error(request)
    if auth_error is not None:
        return auth_error
    try:
        png = await _mysekai(request).render_material_catalog()
    except MysekaiError as exc:
        return error_response(exc.status, exc.code, str(exc), exc.details)
    return Response(content=png, media_type="image/png")


@internal_router.post("/birthday-progress")
async def birthday_progress(payload: BirthdayProgressRequest, request: Request):
    """The birthday party progress strip for one QQ (optionally one role)."""
    auth_error = _internal_auth_error(request)
    if auth_error is not None:
        return auth_error
    try:
        png = await _mysekai(request).birthday_progress_image(
            payload.qq, payload.game_user_id
        )
    except MysekaiError as exc:
        return error_response(exc.status, exc.code, str(exc), exc.details)
    return Response(content=png, media_type="image/png")


@internal_router.post("/highlight-status")
async def highlight_status(payload: QqRequest, request: Request):
    """This QQ's extra highlight material list."""
    auth_error = _internal_auth_error(request)
    if auth_error is not None:
        return auth_error
    try:
        result = await _mysekai(request).highlight_status(payload.qq)
    except MysekaiError as exc:
        return error_response(exc.status, exc.code, str(exc), exc.details)
    return {"ok": True, **result}


@internal_router.post("/highlight-set")
async def highlight_set(payload: HighlightNamesRequest, request: Request):
    """Replace this QQ's extra highlight list with the named materials."""
    auth_error = _internal_auth_error(request)
    if auth_error is not None:
        return auth_error
    try:
        result = await _mysekai(request).set_highlights(
            payload.qq, payload.names, replace=payload.replace
        )
    except MysekaiError as exc:
        return error_response(exc.status, exc.code, str(exc), exc.details)
    return {"ok": True, **result}


@internal_router.post("/highlight-remove")
async def highlight_remove(payload: HighlightNamesRequest, request: Request):
    """Remove named materials from this QQ's extra highlight list."""
    auth_error = _internal_auth_error(request)
    if auth_error is not None:
        return auth_error
    try:
        result = await _mysekai(request).remove_highlights(payload.qq, payload.names)
    except MysekaiError as exc:
        return error_response(exc.status, exc.code, str(exc), exc.details)
    return {"ok": True, **result}


@internal_router.post("/highlight-clear")
async def highlight_clear(payload: QqRequest, request: Request):
    """Drop every extra highlight for this QQ."""
    auth_error = _internal_auth_error(request)
    if auth_error is not None:
        return auth_error
    try:
        result = await _mysekai(request).clear_highlights(payload.qq)
    except MysekaiError as exc:
        return error_response(exc.status, exc.code, str(exc), exc.details)
    return {"ok": True, **result}


@internal_router.post("/convert-done")
async def convert_done(payload: ConvertDoneRequest, request: Request):
    """提醒附带的完成小卡；取不到槽位时由调用方退回纯文字。"""
    auth_error = _internal_auth_error(request)
    if auth_error is not None:
        return auth_error
    service = _mysekai(request)
    try:
        image = await service.convert_done_image(payload.game_user_id, payload.slot_ids)
    except MysekaiError as exc:
        return error_response(exc.status, exc.code, str(exc), exc.details)
    return Response(content=image, media_type="image/png")


@internal_router.post("/watch-reset")
async def water_watch_reset(request: Request):
    """超管一键关闭全群浇水监听。"""
    auth_error = _internal_auth_error(request)
    if auth_error is not None:
        return auth_error
    result = await _mysekai(request).reset_water_watches()
    return {"ok": True, **result}


@internal_router.post("/convert-reset")
async def convert_reset(request: Request):
    """超管一键清空转换机订阅，群友重新订阅后按新逻辑回填。"""
    auth_error = _internal_auth_error(request)
    if auth_error is not None:
        return auth_error
    service = _mysekai(request)
    result = await service.reset_convert_watches()
    return {"ok": True, **result}


@internal_router.post("/convert-ack")
async def convert_ack(payload: ConvertAckRequest, request: Request):
    auth_error = _internal_auth_error(request)
    if auth_error is not None:
        return auth_error
    service = _mysekai(request)
    await service.repository.ack_mysekai_convert_slots(
        payload.game_user_id, payload.slot_ids
    )
    return {"ok": True}


@internal_router.post("/gate")
async def gate_image(payload: GateRequest, request: Request):
    auth_error = _internal_auth_error(request)
    if auth_error is not None:
        return auth_error
    if payload.server != "cn":
        return error_response(422, "unsupported_server", "第一版只支持国服")
    try:
        service = _mysekai(request)
        requested_gate_id = resolve_gate_unit(payload.unit)
        data = await service.fetch_mysekai_gate_data(
            payload.game_user_id, payload.server, requested_gate_id=requested_gate_id
        )
        binding = await service.repository.get_mysekai_binding_by_game(
            payload.server, payload.game_user_id
        )
        meta = await service.catalog_state_meta(payload.game_user_id, payload.server)
        profile = None
        if binding or meta:
            # 与其余模板共用同一块资料卡：等级和两次数据时间都来自本地库。
            profile = {
                "nickname": (binding or {}).get("nickname"),
                "user_id": (binding or {}).get("game_user_id")
                or payload.game_user_id,
                "avatar_base64": (binding or {}).get("leader_avatar"),
            }
            if meta.get("updated_at"):
                profile["updated_at"] = meta["updated_at"]
            if meta.get("mysekai_at"):
                profile["mysekai_at"] = meta["mysekai_at"]
            if meta.get("mysekai_rank") is not None:
                profile["mysekai_rank"] = meta["mysekai_rank"]
            game_rank = meta.get("game_rank")
            if isinstance(game_rank, int):
                profile["game_rank"] = game_rank
        png = await service.render_gate_image(data, profile)
    except MysekaiError as exc:
        return error_response(exc.status, exc.code, str(exc), exc.details)
    return Response(content=png, media_type="image/png")


@dataclass(frozen=True, slots=True)
class _CatalogRender:
    """Which projection to read and which extra options the card needs."""

    kind: str
    options: dict


async def _binding_profile(
    service: MysekaiService,
    server: str,
    game_user_id: str,
    updated_at: str | None = None,
) -> dict | None:
    """The account strip: bound profile fields plus when the data was captured."""
    binding = await service.repository.get_mysekai_binding_by_game(server, game_user_id)
    if not binding and not updated_at:
        return None
    profile: dict = {
        "nickname": (binding or {}).get("nickname"),
        "user_id": (binding or {}).get("game_user_id") or game_user_id,
        "avatar_base64": (binding or {}).get("leader_avatar"),
    }
    if updated_at:
        profile["updated_at"] = updated_at
    return profile


async def _catalog_image(
    request: Request, payload: CatalogRequest, render: _CatalogRender
) -> Response:
    """Fetch the projected snapshot and ask the render service for the card."""
    if payload.server != "cn":
        return error_response(422, "unsupported_server", "第一版只支持国服")
    try:
        service = _mysekai(request)
        if render.kind == "talk-list":
            # 对话在 Haruki 公开面可见，直接用实时快照；copy 一份再补蓝图，
            # 避免把本地字段写进 suite 缓存。
            data = dict(
                await service.fetch_mysekai_suite(
                    payload.game_user_id, render.kind, payload.server
                )
            )
            captured_at = format_capture_time(data.get("upload_time"))
            # 蓝图与已拥有家具只在用户上传的抓包里；没有上传时这里为空，卡片不置灰。
            data.update(
                await service.catalog_hint_payload(payload.game_user_id, payload.server)
            )
        else:
            # 唱片与蓝图公开面不可见，读用户最近一次上传留下的 id 行。
            data = await service.catalog_payload(
                payload.game_user_id, render.kind, payload.server
            )
            captured_at = None
        meta = await service.catalog_state_meta(payload.game_user_id, payload.server)
        profile = await _binding_profile(
            service, payload.server, payload.game_user_id,
            captured_at or meta.get("updated_at"),
        )
        if profile is not None:
            profile.update(_profile_extras(data, meta))
            game_rank = await _game_rank_for(
                service, payload.server, payload.game_user_id, data, meta
            )
            if game_rank is not None:
                profile["game_rank"] = game_rank
        png = await service.render_catalog_image(
            CATALOG_ENDPOINTS[render.kind], data, profile, render.options,
            cache=render.kind != "convert",
        )
    except MysekaiError as exc:
        return error_response(exc.status, exc.code, str(exc), exc.details)
    return Response(content=png, media_type="image/png")


@internal_router.post("/fixture-list")
async def fixture_list(payload: CatalogRequest, request: Request):
    auth_error = _internal_auth_error(request)
    if auth_error is not None:
        return auth_error
    return await _catalog_image(request, payload, _CatalogRender("fixture-list", {}))


@internal_router.post("/talk-list")
async def talk_list(payload: TalkListRequest, request: Request):
    auth_error = _internal_auth_error(request)
    if auth_error is not None:
        return auth_error
    return await _catalog_image(
        request, payload, _CatalogRender("talk-list", {"query": payload.query})
    )


@internal_router.post("/music-record")
async def music_record(payload: MusicRecordRequest, request: Request):
    auth_error = _internal_auth_error(request)
    if auth_error is not None:
        return auth_error
    return await _catalog_image(
        request, payload, _CatalogRender("music-record", {"show_id": payload.show_id})
    )


@internal_router.post("/convert")
async def convert(payload: CatalogRequest, request: Request):
    auth_error = _internal_auth_error(request)
    if auth_error is not None:
        return auth_error
    return await _catalog_image(request, payload, _CatalogRender("convert", {}))


def _suite_game_rank(data: dict) -> int | None:
    """The game level carried by a Haruki suite, when it was fetched at all."""
    gamedata = data.get("userGamedata")
    rank = gamedata.get("rank") if isinstance(gamedata, dict) else None
    return rank if isinstance(rank, int) else None


async def _game_rank_for(
    service: MysekaiService,
    server: str,
    game_user_id: str,
    data: dict,
    meta: dict,
) -> int | None:
    """游戏等级：本次快照带了就顺手落库，否则用绑定/上传时缓存的等级。

    抓包只有烤森等级，所以这个值来自 Haruki 快照；目录卡不额外请求 Haruki。
    """
    stored = meta.get("game_rank")
    if isinstance(stored, int):
        return stored
    suite_rank = _suite_game_rank(data)
    if suite_rank is not None:
        await service.repository.set_mysekai_game_rank(server, game_user_id, suite_rank)
        return suite_rank
    return None


def _profile_extras(data: dict, meta: dict) -> dict:
    """Levels and the 烤森 snapshot time the profile strip shows."""
    extras: dict = {}
    gamedata = data.get("userGamedata")
    if isinstance(gamedata, dict) and isinstance(gamedata.get("rank"), int):
        extras["game_rank"] = gamedata["rank"]
    if meta.get("mysekai_rank") is not None:
        extras["mysekai_rank"] = meta["mysekai_rank"]
    if meta.get("mysekai_at"):
        extras["mysekai_at"] = meta["mysekai_at"]
    return extras


async def _render_catalog_for(
    service: MysekaiService, game_user_id: str, kind: str, query: str
) -> bytes:
    """Shared by the bot route and the public link route."""
    if kind == "talk-list":
        data = dict(await service.fetch_mysekai_suite(game_user_id, kind, "cn"))
        options = {"query": query}
        captured_at = format_capture_time(data.get("upload_time"))
        data.update(await service.catalog_hint_payload(game_user_id, "cn"))
    else:
        data = await service.catalog_payload(game_user_id, kind, "cn")
        options = {}
        captured_at = None
    meta = await service.catalog_state_meta(game_user_id, "cn")
    profile = await _binding_profile(
        service, "cn", game_user_id, captured_at or meta.get("updated_at")
    )
    if profile is not None:
        profile.update(_profile_extras(data, meta))
        game_rank = await _game_rank_for(service, "cn", game_user_id, data, meta)
        if game_rank is not None:
            profile["game_rank"] = game_rank
    return await service.render_catalog_image(
        CATALOG_ENDPOINTS[kind], data, profile, options,
        cache=kind != "convert",
    )


@internal_router.post("/catalog-link")
async def catalog_link(payload: CatalogLinkRequest, request: Request):
    """Return a short-lived URL that opens one full card in a browser."""
    auth_error = _internal_auth_error(request)
    if auth_error is not None:
        return auth_error
    settings = request.app.state.settings
    if payload.server != "cn":
        return error_response(422, "unsupported_server", "第一版只支持国服")
    if not settings.public_base_url:
        return error_response(
            503, "public_base_url_missing", "未配置公网地址，暂时无法生成全量图链接"
        )
    token = issue(
        settings.mysekai_session_secret,
        game_user_id=payload.game_user_id,
        kind=payload.kind,
        query=payload.query,
    )
    return {
        "url": f"{settings.public_base_url}/api/v1/mysekai/catalog/{payload.kind}?t={token}"
    }


@internal_router.post("/master-update")
async def master_update(payload: MasterUpdateRequest, request: Request):
    """Install the newest upstream master snapshot for one region."""
    auth_error = _internal_auth_error(request)
    if auth_error is not None:
        return auth_error
    try:
        return await _mysekai(request).update_master(payload.region)
    except MysekaiError as exc:
        return error_response(exc.status, exc.code, str(exc))


@router.get("/catalog/{kind}")
async def catalog_image(kind: str, t: str, request: Request):
    """Render the full card behind a signed link; no session required."""
    settings = request.app.state.settings
    try:
        claims = parse(settings.mysekai_session_secret, t)
    except CatalogLinkError as failure:
        return error_response(403, "invalid_link", str(failure))
    if claims.get("kind") != kind:
        return error_response(403, "invalid_link", "链接与请求的卡片不一致")
    try:
        png = await _render_catalog_for(
            _mysekai(request), str(claims["gid"]), kind, str(claims.get("q") or "")
        )
    except MysekaiError as exc:
        return error_response(exc.status, exc.code, str(exc), exc.details)
    return Response(content=png, media_type="image/png")


@internal_router.get("/query")
async def query_binding(qq: str, request: Request):
    auth_error = _internal_auth_error(request)
    if auth_error is not None:
        return auth_error
    if not QQ_RE.fullmatch(qq):
        return error_response(422, "invalid_qq", "QQ 号格式不正确")
    binding = await _mysekai(request).query_binding(qq)
    if binding is None:
        return error_response(404, "not_bound", "这个 QQ 还没有绑定游戏 ID")
    roles = await _mysekai(request).list_bindings(qq)
    return {
        "ok": True,
        "binding": _serialize_binding(binding),
        "roles": [_serialize_binding(row, with_avatar=False) for row in roles],
    }


@internal_router.post("/switch-role")
async def switch_role(payload: SwitchRoleRequest, request: Request):
    """Point the QQ at one of its bound roles; queries follow the current one."""
    auth_error = _internal_auth_error(request)
    if auth_error is not None:
        return auth_error
    try:
        binding = await _mysekai(request).switch_role(payload.qq, payload.game_user_id)
    except MysekaiError as exc:
        return error_response(exc.status, exc.code, str(exc), exc.details)
    return {"ok": True, "binding": _serialize_binding(binding)}


@internal_router.post("/unbind")
async def unbind(payload: UnbindRequest, request: Request):
    auth_error = _internal_auth_error(request)
    if auth_error is not None:
        return auth_error
    try:
        binding = await _mysekai(request).unbind(payload.qq, payload.game_user_id)
    except MysekaiError as exc:
        return error_response(exc.status, exc.code, str(exc), exc.details)
    return {"ok": True, "binding": _serialize_binding(binding)}


@router.post("/submit")
async def submit(payload: SubmitRequest, request: Request):
    try:
        _require_session(request)
    except MysekaiError as exc:
        return error_response(exc.status, exc.code, str(exc))
    if payload.region != "cn":
        return error_response(422, "unsupported_server", "第一版只支持国服")
    limiter = request.app.state.services.mysekai_submit_limiter
    allowed, retry_after = limiter.check(_client_ip(request))
    if not allowed:
        return error_response(
            429,
            "rate_limited",
            "上传太频繁，请稍后再试",
            headers={"Retry-After": str(retry_after)},
        )
    try:
        result = await _mysekai(request).submit(
            qq=payload.qq,
            game_user_id=payload.game_user_id,
            server=payload.region,
            file_base64=payload.file_base64,
        )
    except MysekaiError as exc:
        return error_response(exc.status, exc.code, str(exc), exc.details)
    return {"ok": True, **result}


def _ingest_auth_error(request: Request):
    expected = request.app.state.settings.mysekai_ingest_token
    if not expected:
        return error_response(503, "ingest_disabled", "MySekai 上报告口未配置")
    scheme, _, supplied = request.headers.get("Authorization", "").partition(" ")
    valid = scheme.lower() == "bearer" and hmac.compare_digest(
        supplied.encode("utf-8"), expected.encode("utf-8")
    )
    if not valid:
        return error_response(403, "forbidden", "无权调用 MySekai 上报告口")
    return None


def _ingest_allowed(request: Request, game_user_id: str) -> bool:
    allowlist = request.app.state.settings.mysekai_ingest_allowlist
    return not allowlist or game_user_id in allowlist


def _ingest_response(result: dict) -> dict:
    """The mobile capture path accepts both snapshots and delivery results."""
    if result.get("kind") == "birthday_delivery":
        return {
            "ok": True,
            "upload_id": result["upload_id"],
            "kind": "birthday_delivery",
            "message": result.get("message"),
            "push": result["push"],
            "watch": result.get("watch"),
        }
    return {
        "ok": True,
        "upload_id": result["upload_id"],
        "points": result["points"],
        "drops": result["drops"],
        "sites": result["sites"],
        "push": result["push"],
        "watch": result.get("watch"),
    }


@router.post("/ingest")
async def ingest(request: Request):
    """Receive a raw response body from the mobile capture module.

    Deliberately outside ``/internal/``: nginx answers 404 for that prefix, and
    the phone has to reach this endpoint over the public internet. Identity is
    the Bearer token plus the bound game id, never a browser session.
    """
    auth_error = _ingest_auth_error(request)
    if auth_error is not None:
        return auth_error

    game_user_id = (request.headers.get("X-Mysekai-Game-Id") or "").strip()
    if not GAME_ID_RE.fullmatch(game_user_id):
        return error_response(422, "invalid_game_id", "游戏 ID 格式不正确")
    if not _ingest_allowed(request, game_user_id):
        return error_response(403, "not_allowlisted", "这个游戏 ID 不在上报白名单内")

    body = await request.body()
    if not body:
        return error_response(422, "empty_body", "响应体为空")
    if len(body) > MAX_BODY_BYTES:
        return error_response(413, "body_too_large", "响应体超过 4 MB")

    event_kind = request.headers.get("X-Mysekai-Event", "snapshot")
    party_header = request.headers.get("X-Mysekai-Party-Id", "")
    if event_kind not in {"snapshot", "birthday_delivery"}:
        return error_response(422, "invalid_event", "不支持的抓包事件类型")
    party_id = None
    if event_kind == "birthday_delivery":
        if not party_header.isascii() or not party_header.isdecimal() or not 0 < len(party_header) <= 9:
            return error_response(422, "invalid_party_id", "生日活动 ID 格式不正确")
        party_id = int(party_header)
        if party_id <= 0:
            return error_response(422, "invalid_party_id", "生日活动 ID 格式不正确")
    elif party_header:
        return error_response(422, "invalid_party_id", "普通抓包不应带生日活动 ID")

    # 密文长度必须是 16 的倍数；不是就说明手机端把二进制当文本处理坏了。
    # 只记长度与余数，不记内容。
    try:
        result = await _mysekai(request).ingest(
            game_user_id=game_user_id, server="cn", body=body,
            event_kind=event_kind, birthday_party_id=party_id,
        )
    except MysekaiError as exc:
        logger.warning(
            "mysekai ingest failed game_id=%s bytes=%d mod16=%d code=%s",
            game_user_id,
            len(body),
            len(body) % 16,
            exc.code,
        )
        return error_response(exc.status, exc.code, str(exc), exc.details)
    logger.info(
        "mysekai ingest ok game_id=%s bytes=%d mod16=%d upload_id=%s",
        game_user_id,
        len(body),
        len(body) % 16,
        result["upload_id"],
    )
    return _ingest_response(result)


@router.post("/ingest-convert")
async def ingest_convert(request: Request):
    """转换机动作的局部响应：只更新槽位，不出图、不推送。"""
    auth_error = _ingest_auth_error(request)
    if auth_error is not None:
        return auth_error
    if not request.app.state.settings.mysekai_convert_ingest:
        return error_response(503, "convert_ingest_disabled", "转换机上报已关闭")

    game_user_id = (request.headers.get("X-Mysekai-Game-Id") or "").strip()
    if not GAME_ID_RE.fullmatch(game_user_id):
        return error_response(422, "invalid_game_id", "游戏 ID 格式不正确")
    if not _ingest_allowed(request, game_user_id):
        return error_response(403, "not_allowlisted", "这个游戏 ID 不在上报白名单内")

    body = await request.body()
    if not body:
        return error_response(422, "empty_body", "响应体为空")
    if len(body) > MAX_BODY_BYTES:
        return error_response(413, "body_too_large", "响应体超过 4 MB")

    try:
        result = await _mysekai(request).ingest_convert(
            game_user_id=game_user_id, server="cn", body=body
        )
    except MysekaiError as exc:
        return error_response(exc.status, exc.code, str(exc), exc.details)
    return result


# ---------------------------------------------------------------- 模块下载
TEMPLATE_DIR = "app/templates"


def _read_template(settings, name: str) -> str:
    path = settings.project_root / TEMPLATE_DIR / name
    return path.read_text(encoding="utf-8")


@router.get("/module")
async def download_module(request: Request, t: str = ""):
    """Download the Shadowrocket module with this deployment's token baked in.

    Accepts either a browser session (the download link on the page) or the
    ingest token as ``?t=`` — Shadowrocket fetches this itself for one-click
    install and carries no cookies.
    """
    settings = request.app.state.settings
    by_token = bool(t) and hmac.compare_digest(t, settings.mysekai_ingest_token or "")
    if not by_token:
        try:
            _require_session(request)
        except MysekaiError:
            return RedirectResponse("/roomlist", status_code=303)

    if not settings.mysekai_ingest_token:
        return error_response(503, "ingest_disabled", "MySekai 上报告口未配置")

    base = str(request.base_url).rstrip("/")
    body = (
        _read_template(settings, "mysekai.sgmodule")
        .replace("__BASE_URL__", base)
        .replace("__TOKEN__", settings.mysekai_ingest_token)
    )
    return Response(
        content=body,
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="mysekai.sgmodule"'},
    )


@router.get("/capture.js")
async def capture_script(request: Request, t: str = ""):
    """Serve the capture script to Shadowrocket, keyed by the ingest token."""
    settings = request.app.state.settings
    expected = settings.mysekai_ingest_token
    if not expected:
        return error_response(503, "ingest_disabled", "MySekai 上报告口未配置")
    if not t or not hmac.compare_digest(t, expected):
        return error_response(403, "forbidden", "无权获取脚本")

    body = _read_template(settings, "mysekai-capture.js").replace("__TOKEN__", expected)
    return Response(
        content=body,
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/convert.js")
async def convert_script(request: Request, t: str = ""):
    """Serve the converter-action script to Shadowrocket, keyed by the token."""
    settings = request.app.state.settings
    expected = settings.mysekai_ingest_token
    if not expected:
        return error_response(503, "ingest_disabled", "MySekai 上报告口未配置")
    if not t or not hmac.compare_digest(t, expected):
        return error_response(403, "forbidden", "无权获取脚本")

    body = _read_template(settings, "mysekai-convert.js").replace("__TOKEN__", expected)
    return Response(
        content=body,
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/module-link")
async def module_links(request: Request):
    """One-click install URL for Shadowrocket, built from the current request."""
    try:
        _require_session(request)
    except MysekaiError:
        return error_response(401, "session_invalid", "请先输入密码")
    settings = request.app.state.settings
    if not settings.mysekai_ingest_token:
        return error_response(503, "ingest_disabled", "MySekai 上报告口未配置")
    base = str(request.base_url).rstrip("/")
    module_url = f"{base}/api/v1/mysekai/module?t={settings.mysekai_ingest_token}"
    return {
        "ok": True,
        "download_url": module_url,
        "install_url": "shadowrocket://install?module=" + quote(module_url, safe=""),
    }
