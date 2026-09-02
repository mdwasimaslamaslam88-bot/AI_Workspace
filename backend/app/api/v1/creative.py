from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_os.orchestrator import AgentOrchestrator
from app.api.dependencies import get_current_user
from app.creative import CreativeAgent, CreativeAgentError, CreativeSafetyError
from app.creative.service import (
    CreativeConflictError,
    CreativeExperienceService,
    CreativeInputError,
    CreativeNotFoundError,
)
from app.db.dependencies import get_db_session
from app.models.user import User
from app.schemas.creative import (
    CreativeCapabilitiesResponse,
    CreativeExperienceCreateRequest,
    CreativeExperiencePageResponse,
    CreativeExperienceResponse,
    CreativeTurnCreateRequest,
)


router = APIRouter(prefix="/creative", tags=["Creative"])


def _agent(request: Request) -> CreativeAgent:
    orchestrator = getattr(request.app.state, "agent_orchestrator", None)
    if not isinstance(orchestrator, AgentOrchestrator):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Creative local runtime is unavailable",
        )
    return CreativeAgent(orchestrator)


def _response(value) -> CreativeExperienceResponse:
    return CreativeExperienceResponse.model_validate(value, from_attributes=True)


def _raise_creative_error(exc: Exception) -> None:
    if isinstance(exc, CreativeNotFoundError):
        raise HTTPException(status_code=404, detail="Creative experience not found") from None
    if isinstance(exc, CreativeConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if isinstance(exc, CreativeAgentError):
        raise HTTPException(
            status_code=502,
            detail="Verified local creative generation failed",
        ) from None
    if isinstance(exc, (CreativeInputError, CreativeSafetyError)):
        raise HTTPException(
            status_code=422,
            detail="Creative content is outside the available general-audience boundary",
        ) from None
    raise HTTPException(status_code=422, detail="Creative data is invalid") from None


@router.get("/capabilities", response_model=CreativeCapabilitiesResponse)
async def creative_capabilities(
    _current_user: Annotated[User, Depends(get_current_user)],
) -> CreativeCapabilitiesResponse:
    return CreativeCapabilitiesResponse()


@router.get("/experiences", response_model=CreativeExperiencePageResponse)
async def list_creative_experiences(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=20)] = 20,
) -> CreativeExperiencePageResponse:
    values = await CreativeExperienceService(session).list_for_owner(
        current_user.id, limit=limit
    )
    return CreativeExperiencePageResponse(items=[_response(value) for value in values])


@router.post(
    "/experiences",
    response_model=CreativeExperienceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_creative_experience(
    payload: CreativeExperienceCreateRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> CreativeExperienceResponse:
    try:
        value = await CreativeExperienceService(session).create_for_owner(
            current_user.id,
            mode=payload.mode,
            title=payload.title,
            premise=payload.premise,
            genre=payload.genre,
            language=payload.language,
            character_name=payload.character_name,
        )
    except (
        CreativeConflictError,
        CreativeInputError,
        CreativeNotFoundError,
        CreativeSafetyError,
    ) as exc:
        _raise_creative_error(exc)
    return _response(value)


@router.get(
    "/experiences/{experience_id}", response_model=CreativeExperienceResponse
)
async def get_creative_experience(
    experience_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> CreativeExperienceResponse:
    value = await CreativeExperienceService(session).get_for_owner(
        current_user.id, experience_id
    )
    if value is None:
        raise HTTPException(status_code=404, detail="Creative experience not found")
    return _response(value)


@router.post(
    "/experiences/{experience_id}/turns",
    response_model=CreativeExperienceResponse,
)
async def create_creative_turn(
    experience_id: UUID,
    payload: CreativeTurnCreateRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> CreativeExperienceResponse:
    try:
        value = await CreativeExperienceService(session, _agent(request)).add_turn(
            current_user.id, experience_id, payload.owner_input
        )
    except (
        CreativeAgentError,
        CreativeConflictError,
        CreativeInputError,
        CreativeNotFoundError,
        CreativeSafetyError,
    ) as exc:
        _raise_creative_error(exc)
    return _response(value)


@router.post(
    "/experiences/{experience_id}/complete",
    response_model=CreativeExperienceResponse,
)
async def complete_creative_experience(
    experience_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> CreativeExperienceResponse:
    try:
        value = await CreativeExperienceService(session).complete(
            current_user.id, experience_id
        )
    except (CreativeConflictError, CreativeNotFoundError) as exc:
        _raise_creative_error(exc)
    return _response(value)
