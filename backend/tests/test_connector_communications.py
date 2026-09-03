from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.communications import (
    CALLBACK_PATH,
    PHONE_CALL_PATH,
    CommunicationProviderError,
    CommunicationReceipt,
    ConnectorBackedCommunicationProvider,
)
from app.connectors.service import ConnectorConnectionStatus


def _healthy_connector(*capabilities: str):
    return SimpleNamespace(
        connection_status=ConnectorConnectionStatus.HEALTHY,
        scopes=("read", "write"),
        capabilities=capabilities,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "capability", "path"),
    (
        ("start_phone_call", "phone_call", PHONE_CALL_PATH),
        ("schedule_callback", "callback", CALLBACK_PATH),
    ),
)
async def test_connector_provider_executes_bounded_owner_scoped_contract(
    method_name: str,
    capability: str,
    path: str,
):
    owner_id = uuid4()
    connector_id = uuid4()
    request_id = uuid4()
    execution_id = uuid4()
    service = AsyncMock()
    service.get_for_owner.return_value = _healthy_connector(capability)
    service.execute_for_owner.return_value = SimpleNamespace(
        payload={
            "request_id": str(request_id),
            "state": "accepted_by_provider",
        },
        execution=SimpleNamespace(id=execution_id),
    )
    provider = ConnectorBackedCommunicationProvider(service, connector_id)

    receipt = await getattr(provider, method_name)(
        owner_id=owner_id,
        request_id=request_id,
        destination="+14155550123",
        purpose="Owner-approved verification call",
    )

    assert receipt.request_id == request_id
    assert receipt.connector_execution_id == execution_id
    service.get_for_owner.assert_awaited_once_with(owner_id, connector_id)
    service.execute_for_owner.assert_awaited_once_with(
        owner_id,
        connector_id,
        method="POST",
        path=path,
        json_body={
            "request_id": str(request_id),
            "destination": "+14155550123",
            "purpose": "Owner-approved verification call",
        },
        idempotency_key=str(request_id),
        required_capability=capability,
    )


@pytest.mark.asyncio
async def test_connector_provider_fails_closed_without_verified_capability():
    service = AsyncMock()
    service.get_for_owner.return_value = _healthy_connector("records.read")
    provider = ConnectorBackedCommunicationProvider(service, uuid4())

    with pytest.raises(CommunicationProviderError):
        await provider.start_phone_call(
            owner_id=uuid4(),
            request_id=uuid4(),
            destination="+14155550123",
            purpose="Owner-approved verification call",
        )

    service.execute_for_owner.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    (
        {},
        {"request_id": "wrong", "state": "accepted_by_provider"},
        {"request_id": "matching", "state": "queued"},
    ),
)
async def test_connector_provider_rejects_unverified_provider_receipts(payload: dict):
    owner_id = uuid4()
    connector_id = uuid4()
    request_id = uuid4()
    if payload.get("request_id") == "matching":
        payload["request_id"] = str(request_id)
    service = AsyncMock()
    service.get_for_owner.return_value = _healthy_connector("phone_call")
    service.execute_for_owner.return_value = SimpleNamespace(
        payload=payload,
        execution=SimpleNamespace(id=uuid4()),
    )
    provider = ConnectorBackedCommunicationProvider(service, connector_id)

    with pytest.raises(CommunicationProviderError):
        await provider.start_phone_call(
            owner_id=owner_id,
            request_id=request_id,
            destination="+14155550123",
            purpose="Owner-approved verification call",
        )


def test_communication_receipt_requires_audited_connector_execution():
    with pytest.raises(ValueError):
        CommunicationReceipt(  # type: ignore[arg-type]
            request_id=uuid4(),
            connector_execution_id=None,
        )
