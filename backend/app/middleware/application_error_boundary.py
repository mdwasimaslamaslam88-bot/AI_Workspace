from __future__ import annotations

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.exceptions.handlers import unhandled_exception_handler


class ApplicationErrorBoundaryMiddleware:
    """Contain unexpected HTTP application errors before the server boundary."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

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
            await self.app(scope, receive, send)
        except Exception as exc:
            response = await unhandled_exception_handler(Request(scope), exc)
            await response(scope, receive, send)
