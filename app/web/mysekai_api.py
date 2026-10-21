"""HTTP routes for the MySekai 群友入口.

The page talks JSON only (ParoWEB's security middleware requires it), and the
MySekai service stays on loopback: this backend is the only thing that calls it.
"""

from __future__ import annotations

import hmac
import re

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.services.mysekai import (
    COOKIE_NAME,
    SESSION_TTL_SECONDS,
    MysekaiError,
    MysekaiService,
    SessionInvalid,
)
from app.web.errors import error_response


router = APIRouter(prefix="/api/v1/mysekai")

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


class SubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qq: str = Field(pattern=QQ_PATTERN)
    game_user_id: str = Field(pattern=GAME_ID_PATTERN)
    region: str = Field(default="cn", pattern=r"^(cn|jp|tw|kr|en)$")
    file_base64: str = Field(min_length=1, max_length=6_000_000)


def _mysekai(request: Request) -> MysekaiService:
    return request.app.state.services.mysekai


def _require_session(request: Request) -> None:
    if not _mysekai(request).verify_token(request.cookies.get(COOKIE_NAME)):
        raise SessionInvalid("登录已过期，请重新输入密码")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


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


@router.get("/lookup")
async def lookup(qq: str, request: Request):
    try:
        _require_session(request)
    except MysekaiError as exc:
        return error_response(exc.status, exc.code, str(exc))
    if not QQ_RE.fullmatch(qq):
        return error_response(422, "invalid_qq", "QQ 号格式不正确")
    binding = await _mysekai(request).lookup(qq)
    if binding is None:
        return error_response(404, "not_bound", "这个 QQ 还没有绑定游戏 ID")
    return {"ok": True, "binding": _serialize_binding(binding)}


@router.post("/bind")
async def bind(payload: BindRequest, request: Request):
    settings = request.app.state.settings
    if not settings.mysekai_bot_token:
        return error_response(503, "bind_disabled", "绑定接口未配置")
    supplied = request.headers.get("X-Mysekai-Bot-Token", "")
    if not hmac.compare_digest(
        supplied.encode("utf-8"), settings.mysekai_bot_token.encode("utf-8")
    ):
        return error_response(403, "forbidden", "无权调用绑定接口")
    if payload.server != "cn":
        return error_response(422, "unsupported_server", "第一版只支持国服")
    try:
        binding = await _mysekai(request).bind(
            payload.qq, payload.game_user_id, payload.server
        )
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
