from __future__ import annotations

import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.domain.models import DomainError


logger = logging.getLogger(__name__)


def error_response(status_code: int, code: str, message: str, details: dict | None = None, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details or {}}},
        headers=headers,
    )


async def domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
    return error_response(400, exc.code, exc.message, exc.details)


async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    details = {"fields": [{"path": list(error["loc"]), "message": error["msg"]} for error in exc.errors()]}
    return error_response(422, "invalid_request", "请求参数不正确", details)


async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    if request.url.path.startswith("/api/"):
        code = "not_found" if exc.status_code == 404 else "http_error"
        message = "接口不存在" if exc.status_code == 404 else "请求无法处理"
        return error_response(exc.status_code, code, message)
    return error_response(exc.status_code, "not_found", "页面不存在")


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled request error on %s", request.url.path, exc_info=exc)
    return error_response(500, "internal_error", "服务暂时无法处理请求")
