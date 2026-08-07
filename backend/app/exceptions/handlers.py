from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException as StarletteHTTPException, RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

logger = get_logger(__name__)

async def http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    logger.warning(
        "http_exception",
        path=request.url.path,
        status_code=exc.status_code,
        detail=exc.detail,
    )

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": {
                "code": "HTTP_ERROR",
                "message": str(exc.detail),
            },
            "path": request.url.path,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )

async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    logger.warning(
        "validation_error",
        path=request.url.path,
        errors=exc.errors(),
    )

    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Validation failed.",
                "details": exc.errors(),
            },
            "path": request.url.path,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )

async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    logger.exception(
        "unhandled_exception",
        path=request.url.path,
        exception=str(exc),
    )

    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected error occurred.",
            },
            "path": request.url.path,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )

def register_exception_handlers(app: FastAPI) -> None:
    """Register all global exception handlers."""

    app.add_exception_handler(
        StarletteHTTPException,
        http_exception_handler,
    )

    app.add_exception_handler(
        RequestValidationError,
        validation_exception_handler,
    )

    app.add_exception_handler(
        Exception,
        unhandled_exception_handler,
    )