from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
from threading import Lock
from time import monotonic

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class EdgeRateLimitMiddleware:
    """Bound provisioning attempts and repeated API authentication failures.

    The limiter deliberately keys only on the ASGI peer address. It does not
    trust forwarded identity headers, and it never reads or stores credentials.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        auth_failure_limit: int,
        provisioning_limit: int,
        window_seconds: int,
    ) -> None:
        self.app = app
        self.auth_failure_limit = auth_failure_limit
        self.provisioning_limit = provisioning_limit
        self.window_seconds = window_seconds
        self._auth_failures: dict[str, deque[float]] = defaultdict(deque)
        self._provisioning_attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def _clear(self) -> None:
        with self._lock:
            self._auth_failures.clear()
            self._provisioning_attempts.clear()

    def _peer(self, scope: Scope) -> str:
        client = scope.get("client")
        if not client:
            return "unknown"
        return str(client[0])[:128]

    def _purge(self, entries: deque[float], now: float) -> None:
        cutoff = now - self.window_seconds
        while entries and entries[0] <= cutoff:
            entries.popleft()

    def _is_limited(
        self,
        buckets: dict[str, deque[float]],
        peer: str,
        limit: int,
        *,
        record: bool,
    ) -> bool:
        now = monotonic()
        with self._lock:
            entries = buckets[peer]
            self._purge(entries, now)
            if len(entries) >= limit:
                return True
            if record:
                entries.append(now)
            return False

    async def _send_limited(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=429,
            headers={
                "Cache-Control": "no-store",
                "Retry-After": str(self.window_seconds),
            },
            content={
                "success": False,
                "error": {
                    "code": "HTTP_ERROR",
                    "message": "Too many requests. Try again later.",
                },
                "path": scope.get("path", ""),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
        await response(scope, receive, send)

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] == "lifespan":
            self._clear()
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        path = scope.get("path", "")
        peer = self._peer(scope)
        provisioning = method == "POST" and path == "/api/v1/users"

        if provisioning and self._is_limited(
            self._provisioning_attempts,
            peer,
            self.provisioning_limit,
            record=True,
        ):
            await self._send_limited(scope, receive, send)
            return

        protected_api = (
            method != "OPTIONS"
            and path.startswith("/api/v1/")
            and not path.startswith("/api/v1/health")
            and not provisioning
        )
        if protected_api and self._is_limited(
            self._auth_failures,
            peer,
            self.auth_failure_limit,
            record=False,
        ):
            await self._send_limited(scope, receive, send)
            return

        response_status: int | None = None

        async def capture_status(message: Message) -> None:
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = int(message["status"])
            await send(message)

        await self.app(scope, receive, capture_status)
        if protected_api and response_status in {401, 403}:
            self._is_limited(
                self._auth_failures,
                peer,
                self.auth_failure_limit,
                record=True,
            )
