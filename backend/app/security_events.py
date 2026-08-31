from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from threading import Lock


class SecurityEventKind(StrEnum):
    AUTHENTICATION_FAILURE = "authentication_failure"
    RATE_LIMIT_CONTAINMENT = "rate_limit_containment"
    OVERSIZED_REQUEST_CONTAINMENT = "oversized_request_containment"
    APPLICATION_ERROR_CONTAINMENT = "application_error_containment"


@dataclass(frozen=True, slots=True)
class SecurityEvent:
    kind: SecurityEventKind
    occurred_at: datetime


class SecurityEventRecorder:
    """Bounded metadata-only security event buffer; never stores request data."""

    def __init__(self, *, maximum_events: int = 512) -> None:
        if not 1 <= maximum_events <= 4096:
            raise ValueError("security event retention is outside its bound")
        self._events: deque[SecurityEvent] = deque(maxlen=maximum_events)
        self._lock = Lock()

    def record(self, kind: SecurityEventKind) -> None:
        if not isinstance(kind, SecurityEventKind):
            raise TypeError("security event kind is invalid")
        with self._lock:
            self._events.append(SecurityEvent(kind, datetime.now(timezone.utc)))

    def snapshot(self, *, limit: int = 100) -> tuple[SecurityEvent, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("security event result limit is invalid")
        with self._lock:
            return tuple(reversed(tuple(self._events)[-limit:]))
