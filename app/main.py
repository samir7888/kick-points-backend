"""FastAPI application factory and configuration."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from prometheus_client import Counter, Histogram
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from app.api.v1.auth import router as auth_router
from app.api.v1.billing import router as billing_router
from app.api.v1.channels import router as channels_router
from app.api.v1.me import router as me_router
from app.api.v1.ops import router as ops_router
from app.api.v1.payments import router as payments_router
from app.api.v1.points import router as points_router
from app.api.v1.predictions import router as predictions_router
from app.api.v1.settings import router as settings_router

from .api.v1.ops import dependency_readiness
from .api.v1.ops import metrics as metrics_endpoint
from .core.config import get_settings
from .core.errors import (
    AppException,
    app_exception_handler,
    general_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)
from .core.logging import configure_logging, get_logger
from .core.metrics import mark_metrics_process_dead
from .db.session import (
    close_database,
    close_redis,
    get_db_session,
    get_engine,
    get_redis,
)

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests by route and status",
    ("method", "route", "status"),
)
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency by route",
    ("method", "route"),
)


class RequestSizeLimitMiddleware:
    """Bound request-body buffering, including requests without Content-Length.

    Payment-proof uploads (screenshots) legitimately exceed the small default
    cap for JSON bodies, so the upload endpoint gets its own larger limit.
    """

    def __init__(
        self,
        app,
        max_bytes: int,
        upload_path: str,
        max_upload_bytes: int,
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.upload_path = upload_path
        self.max_upload_bytes = max_upload_bytes

    def _limit_for(self, scope) -> int:
        if scope.get("method") == "POST":
            path = str(scope.get("path", ""))
            if path.startswith(self.upload_path):
                return self.max_upload_bytes
        return self.max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        effective_max = self._limit_for(scope)
        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                response = JSONResponse(
                    status_code=400,
                    content={
                        "error": {
                            "code": "VALIDATION_ERROR",
                            "message": "Invalid Content-Length header",
                            "details": {},
                        }
                    },
                )
                await response(scope, receive, send)
                return
            if declared_length > effective_max:
                response = JSONResponse(
                    status_code=413,
                    content={
                        "error": {
                            "code": "VALIDATION_ERROR",
                            "message": "Request body too large",
                            "details": {},
                        }
                    },
                )
                await response(scope, receive, send)
                return

        messages = []
        body_bytes = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.request":
                body_bytes += len(message.get("body", b""))
                if body_bytes > effective_max:
                    response = JSONResponse(
                        status_code=413,
                        content={
                            "error": {
                                "code": "VALIDATION_ERROR",
                                "message": "Request body too large",
                                "details": {},
                            }
                        },
                    )
                    await response(scope, receive, send)
                    return
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                break

        async def replay_receive():
            if messages:
                return messages.pop(0)
            return await receive()

        await self.app(scope, replay_receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan events."""
    logger = get_logger(__name__)

    # Startup
    logger.info("Starting Kick Prediction Backend")
    get_engine()
    await get_redis()
    # Bridge Postgres write notifications -> Redis cache invalidation so cached
    # reads always reflect DB changes from any process (API/worker/listener).
    from app.services.cache_watch import watch_db_changes

    cache_stop = asyncio.Event()
    cache_watcher = asyncio.create_task(watch_db_changes(cache_stop))
    try:
        yield
    finally:
        logger.info("Shutting down Kick Prediction Backend")
        cache_stop.set()
        cache_watcher.cancel()
        try:
            await cache_watcher
        except (asyncio.CancelledError, Exception):
            pass
        await close_redis()
        await close_database()
        mark_metrics_process_dead()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    # Configure logging first
    configure_logging()
    logger = get_logger(__name__)

    settings = get_settings()

    # Create FastAPI app
    app = FastAPI(
        title="Kick Prediction Backend",
        description="SaaS backend for automated Kick.com prediction voting",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
    )
    app.add_middleware(
        RequestSizeLimitMiddleware,
        max_bytes=settings.max_request_body_bytes,
        upload_path="/api/v1/billing/qr-payments",
        max_upload_bytes=settings.max_upload_body_bytes,
    )

    # Add security middleware
    api_host = urlsplit(settings.api_url).hostname
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["*"]
        if not settings.is_production
        else [api_host]
        if api_host
        else [],
    )

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    # Add request logging middleware
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        """Log all HTTP requests."""
        import time

        from structlog.contextvars import bind_contextvars, clear_contextvars

        logger = get_logger(__name__)
        request_id = str(uuid4())
        bind_contextvars(request_id=request_id)

        content_length = request.headers.get("content-length")
        if content_length:
            try:
                upload_limit = (
                    settings.max_upload_body_bytes
                    if request.method == "POST"
                    and request.url.path.startswith(
                        "/api/v1/billing/qr-payments"
                    )
                    else settings.max_request_body_bytes
                )
                if int(content_length) > upload_limit:
                    clear_contextvars()
                    return JSONResponse(
                        status_code=413,
                        content={
                            "error": {
                                "code": "VALIDATION_ERROR",
                                "message": "Request body too large",
                                "details": {},
                            }
                        },
                        headers={"X-Request-ID": request_id},
                    )
            except ValueError:
                clear_contextvars()
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": {
                            "code": "VALIDATION_ERROR",
                            "message": "Invalid Content-Length header",
                            "details": {},
                        }
                    },
                    headers={"X-Request-ID": request_id},
                )

        start = time.perf_counter()
        should_log = request.url.path not in {"/healthz", "/readyz", "/metrics"}
        if should_log:
            logger.info(
                "HTTP request started", method=request.method, path=request.url.path
            )

        try:
            response = await call_next(request)
        except Exception:
            clear_contextvars()
            raise

        route = getattr(request.scope.get("route"), "path", "unmatched")
        HTTP_REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        HTTP_LATENCY.labels(request.method, route).observe(time.perf_counter() - start)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        if should_log:
            logger.info(
                "HTTP request completed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
            )

        clear_contextvars()
        return response

    # Exception handlers
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(ValidationError, validation_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, general_exception_handler)

    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(billing_router, prefix="/api/v1")
    app.include_router(channels_router, prefix="/api/v1")
    app.include_router(me_router, prefix="/api/v1")
    app.include_router(ops_router, prefix="/api/v1")
    app.include_router(payments_router, prefix="/api/v1")
    app.include_router(points_router, prefix="/api/v1")
    app.include_router(predictions_router, prefix="/api/v1")
    app.include_router(settings_router, prefix="/api/v1")

    # Health check endpoint
    @app.get("/healthz", tags=["health"])
    async def health_check() -> dict:
        """Basic health check endpoint."""
        return {"status": "ok"}

    @app.get("/readyz", tags=["health"])
    async def readiness_check(
        db: AsyncSession = Depends(get_db_session),
    ) -> JSONResponse:
        """Readiness check that verifies dependencies."""
        ready, checks = await dependency_readiness(db)
        return JSONResponse(
            {"status": "ready" if ready else "fail", "checks": checks},
            status_code=200 if ready else 503,
        )

    app.add_api_route(
        "/metrics",
        metrics_endpoint,
        methods=["GET"],
        tags=["ops"],
        summary="Prometheus metrics",
        description="Prometheus metrics endpoint restricted to administrators.",
    )

    logger.info("FastAPI application created successfully")
    return app


# Create the app instance
app = create_app()
