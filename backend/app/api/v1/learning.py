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
    LearningAnalyticsResponse,
    LearningAttemptRequest,
    LearningAttemptResponse,
    LearningCapabilitiesResponse,
    LearningEventPageResponse,
    LearningEventResponse,
    LearningHintResponse,
    LearningProfileUpdateRequest,
    LearningProgramCreateRequest,
    LearningProgramPageResponse,
    LearningProgramResponse,
    LearningReviewItemCreateRequest,
    LearningReviewItemResponse,
    LearningReviewRequest,
    LearningSessionCreateRequest,
    LearningSessionResponse,
    LearningSourceCreateRequest,
    LearningStudyPlanResponse,
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
            teaching_mode=payload.teaching_mode,
            preferences=payload.preferences.model_dump(),
            source_document_ids=tuple(payload.source_document_ids),
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


@router.put("/programs/{program_id}/profile", response_model=LearningProgramResponse)
async def update_learning_profile(
    program_id: UUID,
    payload: LearningProfileUpdateRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> LearningProgramResponse:
    try:
        value = await LearningService(session).update_profile(
            current_user.id,
            program_id,
            teaching_mode=payload.teaching_mode,
            preferences=payload.preferences.model_dump(),
        )
    except (LearningInputError, LearningConflictError, LearningNotFoundError) as exc:
        _raise_learning_error(exc)
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
    "/programs/{program_id}/lessons/{lesson_id}/assessment",
    response_model=LearningProgramResponse,
)
async def generate_learning_assessment(
    program_id: UUID,
    lesson_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> LearningProgramResponse:
    try:
        value = await LearningService(session, _teacher(request)).generate_assessment(
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
            skill_name=payload.skill_name,
            grading_mode=payload.grading_mode,
            hints=tuple(payload.hints),
            rubric_keywords=tuple(payload.rubric_keywords),
            source_ids=tuple(payload.source_ids),
            required=payload.required,
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
    "/programs/{program_id}/activities/{activity_id}/hint",
    response_model=LearningHintResponse,
)
async def request_learning_hint(
    program_id: UUID,
    activity_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> LearningHintResponse:
    try:
        hint, remaining = await LearningService(session).request_hint(
            current_user.id, program_id, activity_id
        )
    except (LearningInputError, LearningConflictError, LearningNotFoundError) as exc:
        _raise_learning_error(exc)
    return LearningHintResponse(hint=hint, remaining=remaining)


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


@router.post(
    "/programs/{program_id}/sources",
    response_model=LearningProgramResponse,
    status_code=status.HTTP_201_CREATED,
)
async def attach_learning_source(
    program_id: UUID,
    payload: LearningSourceCreateRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> LearningProgramResponse:
    try:
        value = await LearningService(session).attach_source(
            current_user.id, program_id, payload.document_id
        )
    except (LearningInputError, LearningConflictError, LearningNotFoundError) as exc:
        _raise_learning_error(exc)
    return _program_response(value)


@router.delete(
    "/programs/{program_id}/sources/{source_id}",
    response_model=LearningProgramResponse,
)
async def detach_learning_source(
    program_id: UUID,
    source_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> LearningProgramResponse:
    try:
        value = await LearningService(session).detach_source(
            current_user.id, program_id, source_id
        )
    except (LearningInputError, LearningConflictError, LearningNotFoundError) as exc:
        _raise_learning_error(exc)
    return _program_response(value)


@router.post(
    "/programs/{program_id}/sessions",
    response_model=LearningSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_learning_session(
    program_id: UUID,
    payload: LearningSessionCreateRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> LearningSessionResponse:
    try:
        value = await LearningService(session).start_session(
            current_user.id,
            program_id,
            mode=payload.mode,
            focus=payload.focus,
            planned_minutes=payload.planned_minutes,
            current_lesson_id=payload.current_lesson_id,
        )
    except (LearningInputError, LearningConflictError, LearningNotFoundError) as exc:
        _raise_learning_error(exc)
    return LearningSessionResponse.model_validate(value, from_attributes=True)


@router.post(
    "/programs/{program_id}/sessions/{learning_session_id}/{action}",
    response_model=LearningSessionResponse,
)
async def transition_learning_session(
    program_id: UUID,
    learning_session_id: UUID,
    action: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> LearningSessionResponse:
    try:
        value = await LearningService(session).transition_session(
            current_user.id, program_id, learning_session_id, action
        )
    except (LearningInputError, LearningConflictError, LearningNotFoundError) as exc:
        _raise_learning_error(exc)
    return LearningSessionResponse.model_validate(value, from_attributes=True)


@router.get(
    "/programs/{program_id}/analytics",
    response_model=LearningAnalyticsResponse,
)
async def get_learning_analytics(
    program_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> LearningAnalyticsResponse:
    try:
        value = await LearningService(session).analytics(current_user.id, program_id)
    except (LearningInputError, LearningConflictError, LearningNotFoundError) as exc:
        _raise_learning_error(exc)
    return LearningAnalyticsResponse.model_validate(value)


@router.get(
    "/programs/{program_id}/study-plan",
    response_model=LearningStudyPlanResponse,
)
async def get_learning_study_plan(
    program_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    days: Annotated[int, Query(ge=1, le=7)] = 7,
) -> LearningStudyPlanResponse:
    try:
        values = await LearningService(session).study_plan(
            current_user.id, program_id, days=days
        )
    except (LearningInputError, LearningConflictError, LearningNotFoundError) as exc:
        _raise_learning_error(exc)
    return LearningStudyPlanResponse(items=list(values))


@router.get(
    "/programs/{program_id}/audit",
    response_model=LearningEventPageResponse,
)
async def get_learning_audit(
    program_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> LearningEventPageResponse:
    try:
        values = await LearningService(session).list_events(
            current_user.id, program_id, limit=limit
        )
    except (LearningInputError, LearningConflictError, LearningNotFoundError) as exc:
        _raise_learning_error(exc)
    return LearningEventPageResponse(
        items=[
            LearningEventResponse.model_validate(value, from_attributes=True)
            for value in values
        ]
    )
