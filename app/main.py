from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import Settings
from app.domain.models import DomainError
from app.repositories.sqlite import SQLiteRepository
from app.services.announcements import AnnouncementService
from app.services.catalog import CatalogService
from app.services.draw_service import DrawService
from app.services.mysekai import MysekaiService
from app.services.stats_cache import StatsCache
from app.web.api import router
from app.web.mysekai_api import internal_router as mysekai_internal_router
from app.web.mysekai_api import router as mysekai_router
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
    stats_cache: StatsCache
    mysekai: MysekaiService
    mysekai_auth_limiter: SlidingWindowLimiter
    mysekai_submit_limiter: SlidingWindowLimiter


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    announcements = AnnouncementService(settings.announcements_path)
    catalog = CatalogService(settings.content_dir, settings.images_dir)
    repository = SQLiteRepository(settings.database_path)
    services = Services(
        announcements=announcements,
        catalog=catalog,
        repository=repository,
        sessions=SessionManager(repository, secure=settings.cookie_secure),
        draws=DrawService(
            catalog,
            repository,
            cooldown_seconds=settings.draw_cooldown_seconds,
        ),
        visitor_limiter=SlidingWindowLimiter(limit=30),
        ip_limiter=SlidingWindowLimiter(limit=60),
        stats_cache=StatsCache(settings.stats_cache_seconds),
        mysekai=MysekaiService(settings, repository),
        mysekai_auth_limiter=SlidingWindowLimiter(limit=5, window_seconds=300),
        mysekai_submit_limiter=SlidingWindowLimiter(limit=10, window_seconds=300),
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
            # Token-authenticated, non-browser callers: the Bot and the mobile
            # capture module send no Origin and carry a Bearer token instead.
            is_token_api = request.url.path in {
                "/internal/v1/mysekai/bind",
                "/internal/v1/mysekai/unbind",
                "/internal/v1/mysekai/gate",
                "/internal/v1/mysekai/watch",
                "/internal/v1/mysekai/fixture-list",
                "/internal/v1/mysekai/talk-list",
                "/internal/v1/mysekai/music-record",
                "/api/v1/mysekai/ingest",
            }
            if not is_token_api:
                origin = request.headers.get("origin")
                if origin not in settings.allowed_origins:
                    return error_response(403, "invalid_origin", "请求来源不被允许")
            # The capture module posts the encrypted response body as-is.
            is_binary_upload = request.url.path == "/api/v1/mysekai/ingest"
            if (
                request.method in {"POST", "PUT", "PATCH"}
                and not is_binary_upload
                and not request.headers.get("content-type", "").lower().startswith("application/json")
            ):
                return error_response(415, "json_required", "请求体必须使用 JSON")
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'sha256-UP0QZg7irvSMvOBz9mH2PIIE28+57UiavRfeVea0l3g='; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        if request.url.path.startswith("/static/") and request.url.path.endswith((".html", ".css", ".js")):
            response.headers["Cache-Control"] = "no-cache"
        return response

    application.include_router(router)
    application.include_router(mysekai_router)
    application.include_router(mysekai_internal_router)
    application.mount("/static", StaticFiles(directory=settings.project_root / "app" / "static"), name="static")
    application.mount("/content", StaticFiles(directory=settings.images_dir / "paro_avatars"), name="content")

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
    def index() -> HTMLResponse:
        static_dir = settings.project_root / "app" / "static"
        page = (static_dir / "index.html").read_text(encoding="utf-8")
        for filename in ("app.css", "profile-export.css", "profile-export.js", "export-image.js", "app.js"):
            version = hashlib.sha256((static_dir / filename).read_bytes()).hexdigest()[:16]
            page = page.replace(f'/static/{filename}"', f'/static/{filename}?v={version}"')
        return HTMLResponse(page, headers={"Cache-Control": "no-cache"})

    # 入口名故意中性化，避免被搜到；首页不再挂链接，只私下分发。
    @application.get("/roomlist")
    def roomlist_index() -> HTMLResponse:
        static_dir = settings.project_root / "app" / "static"
        page = (static_dir / "mysekai.html").read_text(encoding="utf-8")
        for filename in ("app.css", "mysekai.css", "mysekai.js"):
            version = hashlib.sha256((static_dir / filename).read_bytes()).hexdigest()[:16]
            page = page.replace(f'/static/{filename}"', f'/static/{filename}?v={version}"')
        return HTMLResponse(page, headers={"Cache-Control": "no-cache"})

    return application


app = create_app()
