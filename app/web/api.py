from __future__ import annotations

import secrets
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.web.errors import error_response
from app.web.sessions import token_hash


router = APIRouter(prefix="/api/v1")


class DrawRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: Annotated[int, Field(strict=True, ge=1, le=3)] = 1
    fixed_side: Literal["none", "akito", "toya"] = "none"
    fixed_name: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_fixed_name(self) -> "DrawRequest":
        if self.fixed_side == "none" and self.fixed_name and self.fixed_name.strip():
            raise ValueError("双方随机时不能指定派生名称")
        if self.fixed_side != "none" and (not self.fixed_name or not self.fixed_name.strip()):
            raise ValueError("固定一方时必须选择派生名称")
        return self


def _services(request: Request):
    return request.app.state.services


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _serialize_profile(profile: dict, snapshot) -> dict:
    outcome_map = snapshot.outcome_map()
    special_counts = [
        {
            "id": outcome.id,
            "label": outcome.label,
            "count": profile["special_counts"].get(outcome.id, 0),
            "asset_urls": list(snapshot.special_assets.get(outcome.id, ())),
        }
        for outcome in snapshot.special_outcomes
    ]
    recent = []
    for item in profile["recent"]:
        special_type = item["special_type"]
        outcome = outcome_map.get(special_type)
        recent.append(
            {
                **item,
                "is_cooking": bool(item["is_cooking"]),
                "counts_as_cooking": bool(item["counts_as_cooking"]),
                "akito_avatar_url": snapshot.avatars["akito"].get(item["akito_name"]) if item["akito_name"] else None,
                "toya_avatar_url": snapshot.avatars["toya"].get(item["toya_name"]) if item["toya_name"] else None,
                "special_label": outcome.label if outcome else None,
                "special_message": outcome.message if outcome else None,
                "special_asset_urls": list(snapshot.special_assets.get(special_type, ())) if special_type else [],
            }
        )
    return {
        "draw_count": profile["draw_count"],
        "cooking_count": profile["cooking_count"],
        "special_counts": special_counts,
        "akito_top": [
            {**item, "avatar_url": snapshot.avatars["akito"].get(item["name"])}
            for item in profile["akito_top"]
        ],
        "toya_top": [
            {**item, "avatar_url": snapshot.avatars["toya"].get(item["name"])}
            for item in profile["toya_top"]
        ],
        "pair_top": [
            {
                **item,
                "akito_avatar_url": snapshot.avatars["akito"].get(item["akito_name"]),
                "toya_avatar_url": snapshot.avatars["toya"].get(item["toya_name"]),
            }
            for item in profile["pair_top"]
        ],
        "recent": recent,
        "recent_total": profile["recent_total"],
        "recent_offset": profile["recent_offset"],
        "recent_limit": profile["recent_limit"],
        "recent_has_more": profile["recent_has_more"],
    }


@router.post("/session")
async def create_session(request: Request, response: Response) -> dict:
    services = _services(request)
    visitor, token, _is_new = await services.sessions.resolve(request)
    services.sessions.set_cookie(response, token)
    return {"visitor": {"created_at": visitor["created_at"]}}


@router.get("/catalog")
async def get_catalog(request: Request, response: Response) -> dict:
    services = _services(request)
    response.headers["Cache-Control"] = "no-store"
    _visitor, token, _is_new = await services.sessions.resolve(request)
    services.sessions.set_cookie(response, token)
    snapshot = services.catalog.snapshot
    response.headers["ETag"] = f'"{snapshot.version}"'
    return {
        "version": snapshot.version,
        "max_draw_count": 3,
        "akito": [
            {"name": name, "avatar_url": snapshot.avatars["akito"].get(name)}
            for name in snapshot.akito_pool
        ],
        "toya": [
            {"name": name, "avatar_url": snapshot.avatars["toya"].get(name)}
            for name in snapshot.toya_pool
        ],
        "special_outcomes": [
            {
                "id": outcome.id,
                "label": outcome.label,
                "message": outcome.message,
                "asset_urls": list(snapshot.special_assets.get(outcome.id, ())),
            }
            for outcome in snapshot.special_outcomes
        ],
    }


@router.get("/announcements")
async def get_announcements(request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return _services(request).announcements.payload()


@router.get("/community-stats")
async def get_community_stats(request: Request, response: Response) -> dict:
    services = _services(request)
    response.headers["Cache-Control"] = "no-store"
    stats = await services.repository.community_stats(limit=10)
    snapshot = services.catalog.snapshot
    return {
        "total_draws": stats["total_draws"],
        "akito_top": [
            {**item, "avatar_url": snapshot.avatars["akito"].get(item["name"])}
            for item in stats["akito_top"]
        ],
        "toya_top": [
            {**item, "avatar_url": snapshot.avatars["toya"].get(item["name"])}
            for item in stats["toya_top"]
        ],
        "pair_top": [
            {
                **item,
                "akito_avatar_url": snapshot.avatars["akito"].get(item["akito_name"]),
                "toya_avatar_url": snapshot.avatars["toya"].get(item["toya_name"]),
            }
            for item in stats["pair_top"]
        ],
    }


@router.post("/draw")
async def create_draw(payload: DrawRequest, request: Request, response: Response):
    services = _services(request)
    visitor, token, _is_new = await services.sessions.resolve(request)
    allowed, retry_after = services.visitor_limiter.check(visitor["id"])
    if allowed:
        allowed, retry_after = services.ip_limiter.check(_client_ip(request))
    if not allowed:
        return error_response(
            429,
            "rate_limited",
            "抽取太快了，请稍后再试",
            {"retry_after": retry_after},
            {"Retry-After": str(retry_after)},
        )
    result = await services.draws.draw(
        visitor_id=visitor["id"],
        count=payload.count,
        fixed_side=payload.fixed_side,
        fixed_name=payload.fixed_name.strip() if payload.fixed_name else None,
    )
    services.sessions.set_cookie(response, token)
    return result


@router.get("/me")
async def get_profile(
    request: Request,
    response: Response,
    recent_offset: int = Query(default=0, ge=0),
    recent_limit: int = Query(default=50, ge=1, le=100),
) -> dict:
    services = _services(request)
    visitor, token, _is_new = await services.sessions.resolve(request)
    profile = await services.repository.profile(
        visitor["id"], recent_limit=recent_limit, recent_offset=recent_offset
    )
    services.sessions.set_cookie(response, token)
    return _serialize_profile(profile, services.catalog.snapshot)


@router.get("/me/stats")
async def get_personal_stats(request: Request, response: Response) -> dict:
    services = _services(request)
    response.headers["Cache-Control"] = "no-store"
    visitor, token, _is_new = await services.sessions.resolve(request)
    profile = await services.repository.profile(
        visitor["id"], recent_limit=0, ranking_limit=10
    )
    snapshot = services.catalog.snapshot
    services.sessions.set_cookie(response, token)
    return {
        "total_draws": profile["draw_count"],
        "akito_top": [
            {**item, "avatar_url": snapshot.avatars["akito"].get(item["name"])}
            for item in profile["akito_top"]
        ],
        "toya_top": [
            {**item, "avatar_url": snapshot.avatars["toya"].get(item["name"])}
            for item in profile["toya_top"]
        ],
        "pair_top": [
            {
                **item,
                "akito_avatar_url": snapshot.avatars["akito"].get(item["akito_name"]),
                "toya_avatar_url": snapshot.avatars["toya"].get(item["toya_name"]),
            }
            for item in profile["pair_top"]
        ],
    }


@router.delete("/me/history")
async def delete_history(request: Request, response: Response) -> dict:
    services = _services(request)
    visitor, _old_token, _is_new = await services.sessions.resolve(request)
    await services.repository.clear_history(visitor["id"])
    new_request_token = secrets.token_urlsafe(32)
    new_visitor = await services.repository.get_or_create_visitor(token_hash(new_request_token))
    services.sessions.set_cookie(response, new_request_token)
    return {"cleared": True, "visitor": {"created_at": new_visitor["created_at"]}}
