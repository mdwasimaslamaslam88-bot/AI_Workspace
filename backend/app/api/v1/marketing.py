from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.connectors import ConnectorRuntime
from app.db.dependencies import get_db_session
from app.marketing.runtime import MarketingCampaignRunner
from app.marketing.service import (
    MarketingAnalyticsInput,
    MarketingCampaignConflictError,
    MarketingCampaignInputError,
    MarketingCampaignNotFoundError,
    MarketingCampaignService,
    MarketingSourceFact,
)
from app.models.user import User
from app.schemas.marketing import (
    MarketingAnalyticsRequest,
    MarketingCampaignCreateRequest,
    MarketingCampaignPageResponse,
    MarketingCampaignResponse,
)


router = APIRouter(prefix="/marketing", tags=["Marketing"])


def _runner(request: Request) -> MarketingCampaignRunner:
    runner = getattr(request.app.state, "marketing_campaign_runner", None)
    if not isinstance(runner, MarketingCampaignRunner):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Marketing execution is not configured",
        )
    return runner


def _runtime(request: Request) -> ConnectorRuntime | None:
    runtime = getattr(request.app.state, "connector_runtime", None)
    return runtime if isinstance(runtime, ConnectorRuntime) else None


def _response(value) -> MarketingCampaignResponse:
    return MarketingCampaignResponse.model_validate(value, from_attributes=True)


def _raise_lifecycle_error(exc: Exception) -> None:
    if isinstance(exc, MarketingCampaignNotFoundError):
        raise HTTPException(status_code=404, detail="Marketing campaign not found") from None
    if isinstance(exc, MarketingCampaignConflictError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from None
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="Marketing campaign data is invalid",
    ) from None


@router.get("/campaigns", response_model=MarketingCampaignPageResponse)
async def list_campaigns(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> MarketingCampaignPageResponse:
    values = await MarketingCampaignService(session).list_for_owner(
        current_user.id, limit=limit
    )
    return MarketingCampaignPageResponse(items=[_response(value) for value in values])


@router.post(
    "/campaigns",
    response_model=MarketingCampaignResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_campaign(
    payload: MarketingCampaignCreateRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> MarketingCampaignResponse:
    try:
        value = await MarketingCampaignService(session, _runtime(request)).create_for_owner(
            current_user.id,
            name=payload.name,
            objective=payload.objective,
            product=payload.product,
            audience=payload.audience,
            channels=tuple(payload.channels),
            source_facts=tuple(
                MarketingSourceFact(source.source_reference, source.fact)
                for source in payload.source_facts
            ),
            publisher_connector_id=payload.publisher_connector_id,
            publish_path=payload.publish_path,
        )
    except (
        MarketingCampaignInputError,
        MarketingCampaignConflictError,
        MarketingCampaignNotFoundError,
    ) as exc:
        _raise_lifecycle_error(exc)
    return _response(value)


@router.get("/campaigns/{campaign_id}", response_model=MarketingCampaignResponse)
async def get_campaign(
    campaign_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> MarketingCampaignResponse:
    value = await MarketingCampaignService(session).get_for_owner(
        current_user.id, campaign_id
    )
    if value is None:
        raise HTTPException(status_code=404, detail="Marketing campaign not found")
    return _response(value)


@router.post(
    "/campaigns/{campaign_id}/start",
    response_model=MarketingCampaignResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_campaign(
    campaign_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> MarketingCampaignResponse:
    owner_id = current_user.id
    await session.rollback()
    try:
        value = await _runner(request).start_for_owner(owner_id, campaign_id)
    except (MarketingCampaignNotFoundError, MarketingCampaignConflictError) as exc:
        _raise_lifecycle_error(exc)
    return _response(value)


@router.post(
    "/campaigns/{campaign_id}/approve",
    response_model=MarketingCampaignResponse,
)
async def approve_campaign(
    campaign_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> MarketingCampaignResponse:
    owner_id = current_user.id
    await session.rollback()
    try:
        value = await _runner(request).approve_and_publish(
            owner_id, campaign_id
        )
    except (MarketingCampaignNotFoundError, MarketingCampaignConflictError) as exc:
        _raise_lifecycle_error(exc)
    return _response(value)


@router.post(
    "/campaigns/{campaign_id}/analytics",
    response_model=MarketingCampaignResponse,
)
async def submit_campaign_analytics(
    campaign_id: UUID,
    payload: MarketingAnalyticsRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> MarketingCampaignResponse:
    owner_id = current_user.id
    await session.rollback()
    try:
        value = await _runner(request).submit_analytics(
            owner_id,
            campaign_id,
            MarketingAnalyticsInput(
                source_reference=payload.source_reference,
                observed_at=payload.observed_at,
                impressions=payload.impressions,
                clicks=payload.clicks,
                conversions=payload.conversions,
                spend_minor=payload.spend_minor,
                revenue_minor=payload.revenue_minor,
            ),
        )
    except (
        MarketingCampaignInputError,
        MarketingCampaignNotFoundError,
        MarketingCampaignConflictError,
    ) as exc:
        _raise_lifecycle_error(exc)
    return _response(value)


@router.delete(
    "/campaigns/{campaign_id}",
    response_model=MarketingCampaignResponse,
)
async def cancel_campaign(
    campaign_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> MarketingCampaignResponse:
    owner_id = current_user.id
    await session.rollback()
    try:
        value = await _runner(request).cancel_for_owner(owner_id, campaign_id)
    except (MarketingCampaignNotFoundError, MarketingCampaignConflictError) as exc:
        _raise_lifecycle_error(exc)
    return _response(value)
