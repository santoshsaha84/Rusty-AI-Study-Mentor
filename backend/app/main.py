"""
FastAPI app factory.
Middleware order matters — security headers first, then logging, then rate limiting.
Safeguarding is called explicitly per route, not as middleware,
so it has access to Firebase and can write flags asynchronously.
"""
import logging
import sys
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.core.config import get_settings
from app.core.limiter import limiter
from app.core.firebase import init_firebase
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.middleware.logging import RequestLoggingMiddleware

# Import safeguarding at module level — if it fails to load, app fails to start
from app.middleware import safeguarding as _safeguarding_check  # noqa: F401

from app.api import admin, auth, health, student, study, test, quiz, teacher


def _configure_logging() -> None:
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer()
            if get_settings().is_development
            else structlog.processors.JSONRenderer(),  # Cloud Logging parses JSON lines
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


_configure_logging()

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    log.info("startup", env=settings.app_env)
    init_firebase()

    # Purge expired rows. Locally, also create any missing tables; staging/production schema
    # comes only from Alembic (the rusty-migrate job) — create_all would race across instances.
    from app.core.database import get_engine, get_session_factory
    if settings.is_development:
        from app.models.rag_trace import RAGTraceRow  # noqa: F401 — registers the model
        from app.models.chapter_summary import ChapterSummary  # noqa: F401
        from app.models.response_cache import ResponseCache  # noqa: F401
        async with get_engine().begin() as conn:
            from app.core.database import Base
            await conn.run_sync(Base.metadata.create_all)

    async with get_session_factory()() as session:
        from app.repositories.trace_repo import TraceRepository
        from app.repositories.cache_repo import CacheRepository
        repo = TraceRepository(session)
        purged = await repo.purge_expired()
        cache_repo = CacheRepository(session)
        cache_purged = await cache_repo.purge_expired()
        await session.commit()
        if purged:
            log.info("rag_trace_ttl_purge", rows_deleted=purged)
        if cache_purged:
            log.info("response_cache_ttl_purge", rows_deleted=cache_purged)

    yield
    log.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Rusty API",
        version="1.0.0",
        # Disable auto-generated docs in production (reduces attack surface)
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
        lifespan=lifespan,
    )

    # Rate limiter setup (shared instance from app.core.limiter)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Security headers — outermost layer
    app.add_middleware(SecurityHeadersMiddleware)

    # Request logging (hashes IPs, never logs query content)
    app.add_middleware(RequestLoggingMiddleware)

    # CORS — strict allowlist only
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # Route registration
    app.include_router(health.router, tags=["health"])
    app.include_router(admin.router, prefix="/admin", tags=["admin"])
    app.include_router(auth.router, prefix="/auth", tags=["auth"])
    app.include_router(student.router, prefix="/student", tags=["student"])
    app.include_router(study.router, prefix="/study", tags=["study"])
    app.include_router(test.router, prefix="/test", tags=["test"])
    app.include_router(quiz.router, prefix="/quiz", tags=["quiz"])
    app.include_router(teacher.router, prefix="/teacher", tags=["teacher"])

    # Friendly error for rate limit (overrides slowapi's default)
    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
        return JSONResponse(
            status_code=429,
            content={"detail": "You're moving fast! Take a moment and try again."},
        )

    # Never expose internal errors to clients
    @app.exception_handler(Exception)
    async def generic_handler(request: Request, exc: Exception):
        log.error("unhandled_exception", error=str(exc), path=request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Rusty is thinking… try again in a moment."},
        )

    return app


app = create_app()
