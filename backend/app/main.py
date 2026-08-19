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

app = FastAPI(
    title=settings.APP_TITLE,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

register_exception_handlers(app)
# Starlette wraps later-added user middleware around earlier entries. Add the
# application error boundary first so it remains innermost, around routing and
# dependencies, while request limits, request IDs, and CORS retain their order.
app.add_middleware(ApplicationErrorBoundaryMiddleware)
app.add_middleware(
    RequestBodyLimitMiddleware,
    max_body_bytes=settings.REQUEST_MAX_BODY_BYTES,
)
app.add_middleware(RequestIDMiddleware)

origins = settings.BACKEND_CORS_ORIGINS

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {
        "name": settings.APP_TITLE,
        "version": settings.APP_VERSION,
        "status": "running",
    }


app.include_router(api_v1_router, prefix="/api/v1")
