"""pjsk-intel 内部接口（工作计划 §13）。

Worker → ParoWEB：ingest / run-status / source-state。
对外查询：latest / history / health / image。
全部 Bearer 鉴权、严格请求模型、幂等校验（§18：常量时间比较）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings

logger = logging.getLogger(__name__)

internal_router = APIRouter(prefix="/internal/v1/pjsk-intel")
query_router = APIRouter(prefix="/internal/v1/pjsk-intel")

MAX_IMAGE_BASE64_CHARS = 12 * 1024 * 1024
SHA_PATTERN = r"^[0-9a-f]{64}$"


class IngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: str = Field(pattern=SHA_PATTERN)
    run_id: str | None = Field(default=None, max_length=64)
    report: dict
    audit: dict | None = None
    image_png_base64: str = Field(min_length=12, max_length=MAX_IMAGE_BASE64_CHARS)


class RunStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    from_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    to_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    status: str = Field(min_length=1, max_length=32)
    error: str | None = Field(default=None, max_length=500)


def _authenticate(settings: Settings, authorization: str | None, expected: str) -> None:
    scheme, _, supplied = (authorization or "").partition(" ")
    if not expected:
        raise HTTPException(status_code=503, detail="pjsk-intel 入口未配置")
    if scheme.lower() != "bearer" or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="无权调用 pjsk-intel 接口")


def _store(request) -> "PjskIntelStore":
    return request.app.state.services.pjsk_intel


def _settings(request) -> Settings:
    return request.app.state.settings


def _canonical(payload: dict) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


# -- Worker → ParoWEB（§13.1）-----------------------------------------------

@internal_router.post("/ingest")
async def ingest(request: Request, payload: IngestRequest, authorization: str | None = Header(default=None)):
    _authenticate(_settings(request), authorization, _settings(request).pjsk_intel_ingest_token)
    report = payload.report
    if report.get("schema") != "intel-report-v1":
        raise HTTPException(status_code=422, detail="报告 schema 不识别")
    if not hmac.compare_digest(
        hashlib.sha256(_canonical(report)).hexdigest(), payload.report_id
    ):
        raise HTTPException(status_code=422, detail="report_id 与报告内容不匹配")
    try:
        result = await _store(request).ingest(
            {
                "report_id": payload.report_id,
                "run_id": payload.run_id,
                "report": report,
                "image_png_base64": payload.image_png_base64,
            }
        )
    except ValueError:
        raise HTTPException(status_code=422, detail="图片不是有效 PNG 或超过大小限制") from None
    if payload.audit:
        # 审计 JSON 随报告长期保留；这里不回传给公开链路（§10.4）。
        logger.info("pjsk-intel ingest %s audit=%s", payload.report_id[:12], bool(payload.audit))
    return result


@internal_router.post("/run-status")
async def run_status(request: Request, payload: RunStatusRequest, authorization: str | None = Header(default=None)):
    _authenticate(_settings(request), authorization, _settings(request).pjsk_intel_ingest_token)
    await _store(request).record_run(
        payload.source,
        payload.run_id,
        payload.from_sha,
        payload.to_sha,
        payload.status,
        payload.error,
    )
    return {"ok": True}


@internal_router.get("/source-state")
async def source_state(request: Request, authorization: str | None = Header(default=None)):
    _authenticate(_settings(request), authorization, _settings(request).pjsk_intel_ingest_token)
    return await _store(request).source_state()


# -- 查询（§13.3；不改状态，只读）---------------------------------------------

@query_router.get("/latest")
async def latest(request: Request, authorization: str | None = Header(default=None)):
    _authenticate(_settings(request), authorization, _settings(request).pjsk_intel_bot_token)
    report = await _store(request).latest_report()
    if report is None:
        raise HTTPException(status_code=404, detail="还没有国服更新报告")
    return report


@query_router.get("/history")
async def history(request: Request, authorization: str | None = Header(default=None), limit: int = 10):
    _authenticate(_settings(request), authorization, _settings(request).pjsk_intel_bot_token)
    return await _store(request).history(limit)


@query_router.get("/health")
async def health(request: Request, authorization: str | None = Header(default=None)):
    _authenticate(_settings(request), authorization, _settings(request).pjsk_intel_bot_token)
    return await _store(request).health()


@query_router.get("/image/{report_id}")
async def report_image(request: Request, report_id: str, authorization: str | None = Header(default=None)):
    _authenticate(_settings(request), authorization, _settings(request).pjsk_intel_bot_token)
    if len(report_id) != 64 or any(char not in "0123456789abcdef" for char in report_id):
        raise HTTPException(status_code=422, detail="report_id 格式无效")
    path = await _store(request).report_image(report_id)
    if path is None:
        raise HTTPException(status_code=404, detail="图片不存在或已过期")
    return FileResponse(path, media_type="image/png")
