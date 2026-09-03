from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.communications import (
    CALLBACK_CAPABILITY,
    PHONE_CALL_CAPABILITY,
    CommunicationProviderError,
    ConnectorBackedCommunicationProvider,
    connector_supports_communication,
)
from app.connectors import ConnectorRuntime, ConnectorService
from app.db.dependencies import get_db_session
from app.models.user import User
from app.schemas.communications import (
    CommunicationAcceptedResponse,
    CommunicationCapabilitiesResponse,
    CommunicationCapability,
    CommunicationRequest,
)


router = APIRouter(prefix="/communications", tags=["Communications"])


def _external_capability(
    configured: bool,
    *dependencies: str,
    connector_ids: tuple[UUID, ...] = (),
) -> CommunicationCapability:
    return CommunicationCapability(
        configured=configured,
        dependencies=list(dependencies),
        connector_ids=list(connector_ids),
    )


def _connector_service(
    request: Request,
    session: AsyncSession,
) -> ConnectorService | None:
    runtime = getattr(request.app.state, "connector_runtime", None)
    if not isinstance(runtime, ConnectorRuntime):
        return None
    return ConnectorService(session, runtime)


@router.get("/capabilities", response_model=CommunicationCapabilitiesResponse)
async def communication_capabilities(
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CommunicationCapabilitiesResponse:
    phone_connector_ids: tuple[UUID, ...] = ()
    callback_connector_ids: tuple[UUID, ...] = ()
    service = _connector_service(request, session)
    if service is not None:
        connectors = await service.list_for_owner(current_user.id)
        phone_connector_ids = tuple(
            connector.id
            for connector in connectors
            if connector_supports_communication(connector, PHONE_CALL_CAPABILITY)
        )
        callback_connector_ids = tuple(
            connector.id
            for connector in connectors
            if connector_supports_communication(connector, CALLBACK_CAPABILITY)
        )
    return CommunicationCapabilitiesResponse(
        phone_call=_external_capability(
            bool(phone_connector_ids),
            "telephony_provider",
            "owner_configuration",
            connector_ids=phone_connector_ids,
        ),
        callback=_external_capability(
            bool(callback_connector_ids),
            "telephony_provider",
            "owner_configuration",
            connector_ids=callback_connector_ids,
        ),
        video=_external_capability(False, "webrtc_provider", "owner_configuration"),
        screen_share=_external_capability(
            False,
            "webrtc_provider",
            "owner_configuration",
        ),
    )


async def _submit(
    *,
    operation: str,
    body: CommunicationRequest,
    request: Request,
    current_user: User,
    session: AsyncSession,
) -> CommunicationAcceptedResponse:
    service = _connector_service(request, session)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="An owner-configured communication provider is required",
        )
    provider = ConnectorBackedCommunicationProvider(service, body.connector_id)
    request_id = uuid4()
    try:
        if operation == "phone_call":
            receipt = await provider.start_phone_call(
                owner_id=current_user.id,
                request_id=request_id,
                destination=body.destination,
                purpose=body.purpose,
            )
        else:
            receipt = await provider.schedule_callback(
                owner_id=current_user.id,
                request_id=request_id,
                destination=body.destination,
                purpose=body.purpose,
            )
        if receipt.request_id != request_id:
            raise CommunicationProviderError("communication receipt did not match request")
    except CommunicationProviderError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The configured communication provider is unavailable",
        ) from None
    return CommunicationAcceptedResponse(
        request_id=request_id,
        state="accepted_by_provider",
        connector_execution_id=receipt.connector_execution_id,
    )


@router.post(
    "/phone-calls",
    response_model=CommunicationAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_phone_call(
    body: CommunicationRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CommunicationAcceptedResponse:
    return await _submit(
        operation="phone_call",
        body=body,
        request=request,
        current_user=current_user,
        session=session,
    )


@router.post(
    "/callbacks",
    response_model=CommunicationAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def schedule_callback(
    body: CommunicationRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CommunicationAcceptedResponse:
    return await _submit(
        operation="callback",
        body=body,
        request=request,
        current_user=current_user,
        session=session,
    )
