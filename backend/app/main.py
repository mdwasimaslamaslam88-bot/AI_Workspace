 # app/main.py

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.lifespan import lifespan
from app.api.v1.router import router as api_v1_router
from app.exceptions.handlers import register_exception_handlers
from app.middleware.application_error_boundary import (
    ApplicationErrorBoundaryMiddleware,
)
from app.middleware.request_body_limit import RequestBodyLimitMiddleware
from app.middleware.request_id import RequestIDMiddleware
from app.middleware.edge_rate_limit import EdgeRateLimitMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.web import mount_web_application


def _api_documentation_urls(remote_gateway_mode: str) -> dict[str, str | None]:
    if remote_gateway_mode == "tailscale":
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {
        "docs_url": "/docs",
        "redoc_url": "/redoc",
        "openapi_url": "/openapi.json",
    }

app = FastAPI(
    title=settings.APP_TITLE,
    version=settings.APP_VERSION,
    lifespan=lifespan,
    **_api_documentation_urls(settings.REMOTE_GATEWAY_MODE),
)

register_exception_handlers(app)
# Starlette wraps later-added user middleware around earlier entries. Add the
# application error boundary first so it remains innermost, around routing and
# dependencies, while request limits, CORS, and request IDs retain their order.
app.add_middleware(ApplicationErrorBoundaryMiddleware)
app.add_middleware(
    RequestBodyLimitMiddleware,
    max_body_bytes=settings.REQUEST_MAX_BODY_BYTES,
)
app.add_middleware(
    EdgeRateLimitMiddleware,
    auth_failure_limit=settings.EDGE_AUTH_FAILURE_LIMIT,
    provisioning_limit=settings.EDGE_PROVISIONING_LIMIT,
    window_seconds=settings.EDGE_RATE_LIMIT_WINDOW_SECONDS,
)
origins = settings.BACKEND_CORS_ORIGINS

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Idempotency-Key",
        "X-Request-ID",
    ],
)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(SecurityHeadersMiddleware)


if settings.WORK_STATION_WEB_ROOT is None:

    @app.get("/")
    def read_root():
        return {
            "name": settings.APP_TITLE,
            "version": settings.APP_VERSION,
            "status": "running",
        }


app.include_router(api_v1_router, prefix="/api/v1")
mount_web_application(app, settings.WORK_STATION_WEB_ROOT)
