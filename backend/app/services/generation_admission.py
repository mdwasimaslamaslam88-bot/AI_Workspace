from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from app.core.config import MAX_GENERATION_ACTIVE_PER_PROCESS


class GenerationAdmissionRejectedError(RuntimeError):
    """The current process cannot admit another generation request."""


class GenerationAdmissionController:
    """Fail-fast, process-local admission for authenticated generation."""

    def __init__(self, max_active: int) -> None:
        if isinstance(max_active, bool) or not isinstance(max_active, int):
            raise TypeError("max_active must be an integer")
        if not 1 <= max_active <= MAX_GENERATION_ACTIVE_PER_PROCESS:
            raise ValueError(
                "max_active must be between 1 and "
                f"{MAX_GENERATION_ACTIVE_PER_PROCESS}"
            )

        self.max_active = max_active
        self._active_users: set[UUID] = set()
        self._active_count = 0
        self._state_lock = asyncio.Lock()

    @asynccontextmanager
    async def admit(self, user_id: UUID) -> AsyncIterator[None]:
        if not isinstance(user_id, UUID):
            raise TypeError("user_id must be a UUID")

        async with self._state_lock:
            if (
                user_id in self._active_users
                or self._active_count >= self.max_active
            ):
                raise GenerationAdmissionRejectedError(
                    "generation capacity is busy"
                )
            self._active_users.add(user_id)
            self._active_count += 1

        try:
            yield
        finally:
            async with self._state_lock:
                self._active_users.remove(user_id)
                self._active_count -= 1
