# app/middleware/request_id.py

from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import (
    BaseHTTPMiddleware,
    RequestResponseEndpoint,
)
from starlette.responses import Response


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a supplied or generated request identifier to each exchange."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid4())

        request.state.request_id = request_id

        response = await call_next(request)

        response.headers["X-Request-ID"] = request_id

        return response
