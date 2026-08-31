from __future__ import annotations

from datetime import datetime, timezone

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import MAX_REQUEST_BODY_BYTES
from app.security_events import SecurityEventKind, SecurityEventRecorder


class _RequestBodyTooLarge(BaseException):
    """Stop request processing without FastAPI converting this to HTTP 400."""


class RequestBodyLimitMiddleware:
    """Reject oversized HTTP request bodies without buffering them first."""

    def __init__(self, app: ASGIApp, max_body_bytes: int) -> None:
        if isinstance(max_body_bytes, bool) or not isinstance(max_body_bytes, int):
            raise TypeError("max_body_bytes must be an integer")
        if not 1 <= max_body_bytes <= MAX_REQUEST_BODY_BYTES:
            raise ValueError(
                "max_body_bytes must be between 1 and "
                f"{MAX_REQUEST_BODY_BYTES}"
            )

        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        try:
            declared_length = _content_length(scope)
        except ValueError:
            await _send_http_error(
                scope,
                receive,
                send,
                status_code=400,
                message="Invalid request headers",
            )
            return

        if (
            declared_length is not None
            and declared_length > self.max_body_bytes
        ):
            await _send_http_error(
                scope,
                receive,
                send,
                status_code=413,
                message="Request body is too large",
            )
            return

        received_bytes = 0

        async def limited_receive() -> Message:
            nonlocal received_bytes

            message = await receive()
            if message["type"] != "http.request":
                return message

            body = message.get("body", b"")
            if len(body) > self.max_body_bytes - received_bytes:
                raise _RequestBodyTooLarge
            received_bytes += len(body)
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestBodyTooLarge:
            await _send_http_error(
                scope,
                receive,
                send,
                status_code=413,
                message="Request body is too large",
            )


def _content_length(scope: Scope) -> int | None:
    values = [
        value
        for name, value in scope.get("headers", ())
        if name.lower() == b"content-length"
    ]
    if not values:
        return None

    parsed: list[int] = []
    for value in values:
        if b"," in value:
            raise ValueError("ambiguous Content-Length")
        normalized = value.strip(b" \t")
        if not normalized or not normalized.isdigit():
            raise ValueError("invalid Content-Length")
        parsed.append(int(normalized))

    if len(set(parsed)) != 1:
        raise ValueError("conflicting Content-Length values")
    return parsed[0]


async def _send_http_error(
    scope: Scope,
    receive: Receive,
    send: Send,
    *,
    status_code: int,
    message: str,
) -> None:
    if status_code == 413:
        application = scope.get("app")
        recorder = getattr(getattr(application, "state", None), "security_event_recorder", None)
        if isinstance(recorder, SecurityEventRecorder):
            recorder.record(SecurityEventKind.OVERSIZED_REQUEST_CONTAINMENT)
    response = JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "error": {
                "code": "HTTP_ERROR",
                "message": message,
            },
            "path": scope.get("path", ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )
    await response(scope, receive, send)
