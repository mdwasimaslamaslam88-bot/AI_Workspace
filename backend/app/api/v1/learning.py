from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_os.orchestrator import AgentOrchestrator
from app.api.dependencies import get_current_user
from app.db.dependencies import get_db_session
from app.learning import LearningTeacherAgent, LearningTeacherError
from app.learning.service import (
    LearningConflictError,
    LearningInputError,
    LearningNotFoundError,
    LearningService,
)
from app.models.user import User
from app.schemas.learning import (
    LearningActivityCreateRequest,
    LearningAttemptRequest,
    LearningAttemptResponse,
    LearningCapabilitiesResponse,
    LearningProgramCreateRequest,
    LearningProgramPageResponse,
    LearningProgramResponse,
    LearningReviewItemCreateRequest,
    LearningReviewItemResponse,
    LearningReviewRequest,
)


router = APIRouter(prefix="/learning", tags=["Learning"])


def _teacher(request: Request) -> LearningTeacherAgent:
    orchestrator = getattr(request.app.state, "agent_orchestrator", None)
    if not isinstance(orchestrator, AgentOrchestrator):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Learning teacher runtime is unavailable",
        )
    return LearningTeacherAgent(orchestrator)


def _program_response(value) -> LearningProgramResponse:
    return LearningProgramResponse.model_validate(value, from_attributes=True)


def _raise_learning_error(exc: Exception) -> None:
    if isinstance(exc, LearningNotFoundError):
        raise HTTPException(status_code=404, detail="Learning resource not found") from None
    if isinstance(exc, LearningConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if isinstance(exc, LearningTeacherError):
        raise HTTPException(
            status_code=502, detail="Verified local learning generation failed"
        ) from None
    raise HTTPException(status_code=422, detail="Learning data is invalid") from None


@router.get("/capabilities", response_model=LearningCapabilitiesResponse)
async def learning_capabilities(
    _current_user: Annotated[User, Depends(get_current_user)],
) -> LearningCapabilitiesResponse:
    return LearningCapabilitiesResponse()


@router.get("/programs", response_model=LearningProgramPageResponse)
async def list_learning_programs(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=20)] = 20,
) -> LearningProgramPageResponse:
    values = await LearningService(session).list_programs(current_user.id, limit=limit)
    return LearningProgramPageResponse(items=[_program_response(value) for value in values])


@router.post(
    "/programs",
    response_model=LearningProgramResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_learning_program(
    payload: LearningProgramCreateRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> LearningProgramResponse:
    try:
        value = await LearningService(session).create_program(
            current_user.id,
            subject=payload.subject,
            goal=payload.goal,
            target_language=payload.target_language,
            instruction_language=payload.instruction_language,
            start_difficulty=payload.start_difficulty,
            target_difficulty=payload.target_difficulty,
            weekly_minutes=payload.weekly_minutes,
            adaptive_difficulty=payload.adaptive_difficulty,
        )
    except (LearningInputError, LearningConflictError, LearningNotFoundError) as exc:
        _raise_learning_error(exc)
    return _program_response(value)


@router.get("/programs/{program_id}", response_model=LearningProgramResponse)
async def get_learning_program(
    program_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> LearningProgramResponse:
    value = await LearningService(session).get_program(current_user.id, program_id)
    if value is None:
        raise HTTPException(status_code=404, detail="Learning resource not found")
    return _program_response(value)


@router.post(
    "/programs/{program_id}/lessons/{lesson_id}/generate",
    response_model=LearningProgramResponse,
)
async def generate_learning_lesson(
    program_id: UUID,
    lesson_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> LearningProgramResponse:
    try:
        value = await LearningService(session, _teacher(request)).generate_lesson(
            current_user.id, program_id, lesson_id
        )
    except (
        LearningConflictError,
        LearningNotFoundError,
        LearningTeacherError,
    ) as exc:
        _raise_learning_error(exc)
    return _program_response(value)


@router.post(
    "/programs/{program_id}/lessons/{lesson_id}/activities",
    response_model=LearningProgramResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_learning_activity(
    program_id: UUID,
    lesson_id: UUID,
    payload: LearningActivityCreateRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> LearningProgramResponse:
    try:
        value = await LearningService(session).create_activity(
            current_user.id,
            program_id,
            lesson_id,
            kind=payload.kind,
            prompt=payload.prompt,
            expected_answer=payload.expected_answer,
            explanation=payload.explanation,
            difficulty=payload.difficulty,
            max_attempts=payload.max_attempts,
        )
    except (LearningInputError, LearningConflictError, LearningNotFoundError) as exc:
        _raise_learning_error(exc)
    return _program_response(value)


@router.post(
    "/programs/{program_id}/activities/{activity_id}/attempts",
    response_model=LearningAttemptResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_learning_attempt(
    program_id: UUID,
    activity_id: UUID,
    payload: LearningAttemptRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> LearningAttemptResponse:
    try:
        value = await LearningService(session).submit_attempt(
            current_user.id, program_id, activity_id, payload.answer
        )
    except (LearningInputError, LearningConflictError, LearningNotFoundError) as exc:
        _raise_learning_error(exc)
    return LearningAttemptResponse.model_validate(value, from_attributes=True)


@router.post(
    "/programs/{program_id}/review-items",
    response_model=LearningReviewItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_learning_review_item(
    program_id: UUID,
    payload: LearningReviewItemCreateRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> LearningReviewItemResponse:
    try:
        value = await LearningService(session).add_review_item(
            current_user.id, program_id, front=payload.front, back=payload.back
        )
    except (LearningInputError, LearningConflictError, LearningNotFoundError) as exc:
        _raise_learning_error(exc)
    return LearningReviewItemResponse.model_validate(value, from_attributes=True)


@router.post(
    "/programs/{program_id}/review-items/{item_id}/reviews",
    response_model=LearningReviewItemResponse,
)
async def review_learning_item(
    program_id: UUID,
    item_id: UUID,
    payload: LearningReviewRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> LearningReviewItemResponse:
    try:
        value = await LearningService(session).review_item(
            current_user.id, program_id, item_id, payload.quality
        )
    except (LearningInputError, LearningConflictError, LearningNotFoundError) as exc:
        _raise_learning_error(exc)
    return LearningReviewItemResponse.model_validate(value, from_attributes=True)
