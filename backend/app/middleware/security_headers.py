from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


_CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "base-uri 'none'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' blob: data:",
        "media-src 'self' blob: data:",
        "font-src 'self'",
        "connect-src 'self' http://127.0.0.1:8000 http://localhost:8000",
        "worker-src 'self'",
    )
)


class SecurityHeadersMiddleware:
    """Apply a fixed browser security policy without reflecting request data."""

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

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = _CONTENT_SECURITY_POLICY
                headers["Cross-Origin-Opener-Policy"] = "same-origin"
                headers["Permissions-Policy"] = (
                    "camera=(self), microphone=(self), geolocation=(), "
                    "payment=(), usb=()"
                )
                headers["Referrer-Policy"] = "no-referrer"
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                if scope.get("scheme") == "https":
                    headers["Strict-Transport-Security"] = (
                        "max-age=31536000; includeSubDomains"
                    )
            await send(message)

        await self.app(scope, receive, send_with_security_headers)
