from __future__ import annotations

from typing import Any
from uuid import UUID

from app.communications.base import CommunicationProviderError, CommunicationReceipt
from app.connectors.service import (
    ConnectorConnectionStatus,
    ConnectorExecutionError,
    ConnectorNotFoundError,
    ConnectorService,
)


PHONE_CALL_CAPABILITY = "phone_call"
CALLBACK_CAPABILITY = "callback"
PHONE_CALL_PATH = "/communications/phone-calls"
CALLBACK_PATH = "/communications/callbacks"


def connector_supports_communication(
    connector: Any,
    capability: str,
) -> bool:
    return bool(
        connector.connection_status is ConnectorConnectionStatus.HEALTHY
        and "write" in connector.scopes
        and capability in connector.capabilities
    )


class ConnectorBackedCommunicationProvider:
    """Owner-scoped communication gateway over the audited connector runtime."""

    def __init__(self, service: ConnectorService, connector_id: UUID) -> None:
        self._service = service
        self._connector_id = connector_id

    async def start_phone_call(
        self,
        *,
        owner_id: UUID,
        request_id: UUID,
        destination: str,
        purpose: str,
    ) -> CommunicationReceipt:
        return await self._execute(
            owner_id=owner_id,
            request_id=request_id,
            destination=destination,
            purpose=purpose,
            capability=PHONE_CALL_CAPABILITY,
            path=PHONE_CALL_PATH,
        )

    async def schedule_callback(
        self,
        *,
        owner_id: UUID,
        request_id: UUID,
        destination: str,
        purpose: str,
    ) -> CommunicationReceipt:
        return await self._execute(
            owner_id=owner_id,
            request_id=request_id,
            destination=destination,
            purpose=purpose,
            capability=CALLBACK_CAPABILITY,
            path=CALLBACK_PATH,
        )

    async def _execute(
        self,
        *,
        owner_id: UUID,
        request_id: UUID,
        destination: str,
        purpose: str,
        capability: str,
        path: str,
    ) -> CommunicationReceipt:
        try:
            connector = await self._service.get_for_owner(
                owner_id,
                self._connector_id,
            )
            if not connector_supports_communication(connector, capability):
                raise CommunicationProviderError(
                    "connector is not health-verified for this communication"
                )
            result = await self._service.execute_for_owner(
                owner_id,
                self._connector_id,
                method="POST",
                path=path,
                json_body={
                    "request_id": str(request_id),
                    "destination": destination,
                    "purpose": purpose,
                },
                idempotency_key=str(request_id),
                required_capability=capability,
            )
        except CommunicationProviderError:
            raise
        except (ConnectorExecutionError, ConnectorNotFoundError, ValueError, TypeError):
            raise CommunicationProviderError(
                "connector communication execution failed"
            ) from None

        payload = result.payload
        if (
            not isinstance(payload, dict)
            or payload.get("request_id") != str(request_id)
            or payload.get("state") != "accepted_by_provider"
        ):
            raise CommunicationProviderError(
                "connector communication receipt was not verified"
            )
        return CommunicationReceipt(
            request_id=request_id,
            connector_execution_id=result.execution.id,
        )
