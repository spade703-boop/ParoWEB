"""HTTP routes for the MySekai 群友入口.

The page talks JSON only (ParoWEB's security middleware requires it), and the
MySekai service stays on loopback: this backend is the only thing that calls it.
"""

from __future__ import annotations

import hmac
import logging
import re
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


class SubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qq: str = Field(pattern=QQ_PATTERN)
    game_user_id: str = Field(pattern=GAME_ID_PATTERN)
    region: str = Field(default="cn", pattern=r"^(cn|jp|tw|kr|en)$")
    file_base64: str = Field(min_length=1, max_length=6_000_000)


class QqRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qq: str = Field(pattern=QQ_PATTERN)


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


def _serialize_binding(binding: dict) -> dict:
    avatar = binding.get("leader_avatar")
    return {
        "qq": binding["qq"],
        "server": binding["server"],
        "game_user_id": binding["game_user_id"],
        "nickname": binding.get("nickname"),
        "deck_id": binding.get("deck_id"),
        "leader_card_id": binding.get("leader_card_id"),
        "avatar_data_url": f"data:image/png;base64,{avatar}" if avatar else None,
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
    await service.repository.set_mysekai_water_watch(payload.qq, payload.enabled)
    birthday = await service.fetch_birthday_state() if payload.enabled else None
    return {"ok": True, "enabled": payload.enabled, "birthday": birthday}


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
        profile = None
        if binding:
            profile = {
                "nickname": binding.get("nickname"),
                "user_id": binding.get("game_user_id"),
                "avatar_base64": binding.get("leader_avatar"),
            }
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
            # 蓝图只在用户上传的抓包里；没有上传时这里为空，卡片不置灰。
            data.update(
                await service.blueprint_payload(payload.game_user_id, payload.server)
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
        png = await service.render_catalog_image(
            CATALOG_ENDPOINTS[render.kind], data, profile, render.options
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
        data.update(await service.blueprint_payload(game_user_id, "cn"))
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
    return await service.render_catalog_image(
        CATALOG_ENDPOINTS[kind], data, profile, options
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
    return {"ok": True, "binding": _serialize_binding(binding)}


@internal_router.post("/unbind")
async def unbind(payload: QqRequest, request: Request):
    auth_error = _internal_auth_error(request)
    if auth_error is not None:
        return auth_error
    try:
        binding = await _mysekai(request).unbind(payload.qq)
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

    # 密文长度必须是 16 的倍数；不是就说明手机端把二进制当文本处理坏了。
    # 只记长度与余数，不记内容。
    try:
        result = await _mysekai(request).ingest(
            game_user_id=game_user_id, server="cn", body=body
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
    return {
        "ok": True,
        "upload_id": result["upload_id"],
        "points": result["points"],
        "drops": result["drops"],
        "sites": result["sites"],
        "push": result["push"],
    }


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
