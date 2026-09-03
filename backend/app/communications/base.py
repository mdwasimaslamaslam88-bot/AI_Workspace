from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


class CommunicationProviderError(RuntimeError):
    """A configured provider failed without exposing owner or provider data."""


@dataclass(frozen=True, slots=True)
class CommunicationReceipt:
    request_id: UUID
    connector_execution_id: UUID
    state: str = "accepted_by_provider"

    def __post_init__(self) -> None:
        if (
            self.state != "accepted_by_provider"
            or not isinstance(self.request_id, UUID)
            or not isinstance(self.connector_execution_id, UUID)
        ):
            raise ValueError("communication receipt state is invalid")


class RealtimeCommunicationProvider(Protocol):
    async def start_phone_call(
        self,
        *,
        owner_id: UUID,
        request_id: UUID,
        destination: str,
        purpose: str,
    ) -> CommunicationReceipt: ...

    async def schedule_callback(
        self,
        *,
        owner_id: UUID,
        request_id: UUID,
        destination: str,
        purpose: str,
    ) -> CommunicationReceipt: ...
