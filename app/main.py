from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import Settings
from app.domain.models import DomainError
from app.repositories.sqlite import SQLiteRepository
from app.services.announcements import AnnouncementService
from app.services.catalog import CatalogService
from app.services.draw_service import DrawService
from app.web.api import router
from app.web.errors import (
    domain_error_handler,
    error_response,
    http_error_handler,
    unexpected_error_handler,
    validation_error_handler,
)
from app.web.rate_limit import SlidingWindowLimiter
from app.web.sessions import SessionManager


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Services:
    announcements: AnnouncementService
    catalog: CatalogService
    repository: SQLiteRepository
    sessions: SessionManager
    draws: DrawService
    visitor_limiter: SlidingWindowLimiter
    ip_limiter: SlidingWindowLimiter


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    announcements = AnnouncementService(settings.announcements_path)
    catalog = CatalogService(settings.content_dir)
    repository = SQLiteRepository(settings.database_path)
    services = Services(
        announcements=announcements,
        catalog=catalog,
        repository=repository,
        sessions=SessionManager(repository, secure=settings.cookie_secure),
        draws=DrawService(catalog, repository),
        visitor_limiter=SlidingWindowLimiter(limit=30),
        ip_limiter=SlidingWindowLimiter(limit=60),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if announcements.path.exists():
            try:
                announcements.load()
            except Exception:
                logger.exception("Announcement file could not be loaded; starting without announcements")
        catalog.load()
        await repository.initialize()
        yield

    application = FastAPI(
        title="抽派生",
        version="0.1.0",
        docs_url=None if settings.environment == "production" else "/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    application.state.services = services
    application.state.settings = settings
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
    application.add_exception_handler(DomainError, domain_error_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    application.add_exception_handler(HTTPException, http_error_handler)
    application.add_exception_handler(Exception, unexpected_error_handler)

    @application.middleware("http")
    async def security_middleware(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin not in settings.allowed_origins:
                return error_response(403, "invalid_origin", "请求来源不被允许")
            if request.method in {"POST", "PUT", "PATCH"} and not request.headers.get("content-type", "").lower().startswith("application/json"):
                return error_response(415, "json_required", "请求体必须使用 JSON")
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        return response

    application.include_router(router)
    application.mount("/static", StaticFiles(directory=settings.project_root / "app" / "static"), name="static")
    application.mount("/content", StaticFiles(directory=settings.content_dir / "images" / "paro_avatars"), name="content")

    @application.get("/healthz")
    async def healthcheck():
        content_ok = True
        try:
            catalog.snapshot
        except Exception:
            content_ok = False
        database_ok = await repository.healthcheck()
        healthy = content_ok and database_ok
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={"status": "ok" if healthy else "unhealthy", "database": database_ok, "content": content_ok},
        )

    @application.get("/")
    async def index() -> FileResponse:
        return FileResponse(settings.project_root / "app" / "static" / "index.html")

    return application


app = create_app()
