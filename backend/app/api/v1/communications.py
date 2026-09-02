from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies import get_current_user
from app.communications import CommunicationProviderError
from app.models.user import User
from app.schemas.communications import (
    CommunicationAcceptedResponse,
    CommunicationCapabilitiesResponse,
    CommunicationCapability,
    CommunicationRequest,
)


router = APIRouter(prefix="/communications", tags=["Communications"])


def _provider(request: Request):
    return getattr(request.app.state, "realtime_communication_provider", None)


def _external_capability(configured: bool, *dependencies: str) -> CommunicationCapability:
    return CommunicationCapability(
        configured=configured,
        dependencies=list(dependencies),
    )


@router.get("/capabilities", response_model=CommunicationCapabilitiesResponse)
async def communication_capabilities(
    request: Request,
    _current_user: Annotated[User, Depends(get_current_user)],
) -> CommunicationCapabilitiesResponse:
    configured = _provider(request) is not None
    return CommunicationCapabilitiesResponse(
        phone_call=_external_capability(
            configured,
            "telephony_provider",
            "owner_configuration",
        ),
        callback=_external_capability(
            configured,
            "telephony_provider",
            "owner_configuration",
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
) -> CommunicationAcceptedResponse:
    provider = _provider(request)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="An owner-configured communication provider is required",
        )
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
) -> CommunicationAcceptedResponse:
    return await _submit(
        operation="phone_call",
        body=body,
        request=request,
        current_user=current_user,
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
) -> CommunicationAcceptedResponse:
    return await _submit(
        operation="callback",
        body=body,
        request=request,
        current_user=current_user,
    )
