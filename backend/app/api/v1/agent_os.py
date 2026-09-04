from __future__ import annotations

import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.agent_os.contracts import AgentPermission, AgentRunRequest, AgentRunStatus
from app.agent_os.runtime import (
    AgentRunManager,
    AgentRunConflictError,
    AgentRunNotFoundError,
    AgentRunRecord,
)
from app.api.dependencies import get_current_user
from app.models.user import User
from app.schemas.agent_os import (
    AgentAttemptResponse,
    AgentOSCapabilitiesResponse,
    AgentPlanStepResponse,
    AgentProfileResponse,
    AgentRunCreateRequest,
    AgentRunModifyRequest,
    AgentRunPageResponse,
    AgentRunResponse,
    AgentRunEventResponse,
    AgentVerificationCheckResponse,
)


router = APIRouter(prefix="/agent-os", tags=["Agent OS"])


def _manager(request: Request) -> AgentRunManager:
    manager = getattr(request.app.state, "agent_run_manager", None)
    if not isinstance(manager, AgentRunManager):
        raise RuntimeError("Agent OS is not configured")
    return manager


def _response(record: AgentRunRecord) -> AgentRunResponse:
    result = record.result
    plan = record.plan or (result.plan if result is not None else None)
    return AgentRunResponse(
        id=record.id,
        goal=record.goal,
        source=record.source,
        task=record.task,
        specialist=record.specialist,
        status=record.status,
        created_at=record.created_at,
        updated_at=record.updated_at,
        output=result.output if result is not None else None,
        failure_code=result.failure_code if result is not None else None,
        plan=[
            AgentPlanStepResponse(
                step_id=step.step_id,
                agent=step.agent,
                task=step.task,
                permissions=sorted(step.permissions, key=lambda item: item.value),
                requires_objective_evidence=step.requires_objective_evidence,
            )
            for step in (plan.steps if plan is not None else ())
        ],
        events=[
            AgentRunEventResponse(
                sequence=event.sequence,
                status=event.status,
                created_at=event.created_at,
                step_id=event.step_id,
                attempt=event.attempt,
                agent=event.agent,
                model_id=event.model_id,
                action=event.action,
                detail_sha256=event.detail_sha256,
            )
            for event in record.events
        ],
        attempts=[
            AgentAttemptResponse(
                step_id=attempt.step_id,
                attempt=attempt.attempt,
                agent=attempt.agent,
                model_id=attempt.model_id,
                verified=attempt.verification.passed,
                output_sha256=attempt.verification.output_sha256,
                checks=[
                    AgentVerificationCheckResponse(
                        check_id=check.check_id,
                        passed=check.passed,
                        failure=check.failure,
                        evidence_sha256=check.evidence_sha256,
                    )
                    for check in attempt.verification.checks
                ],
            )
            for attempt in (result.attempts if result is not None else ())
        ],
        pause_requested=record.pause_requested,
        requires_approval=record.requires_approval,
        approved=record.approved,
        revision=record.revision,
        manual_retry_count=record.manual_retry_count,
        can_pause=(
            not record.pause_requested
            and record.status
            in {
                AgentRunStatus.QUEUED,
                AgentRunStatus.PLANNING,
                AgentRunStatus.RUNNING,
                AgentRunStatus.VERIFYING,
                AgentRunStatus.RETRYING,
            }
        ),
        can_resume=record.status is AgentRunStatus.PAUSED,
        can_approve=record.status is AgentRunStatus.NEEDS_APPROVAL,
        can_modify=record.status
        in {
            AgentRunStatus.QUEUED,
            AgentRunStatus.NEEDS_APPROVAL,
            AgentRunStatus.PLANNING,
            AgentRunStatus.RUNNING,
            AgentRunStatus.PAUSED,
            AgentRunStatus.VERIFYING,
            AgentRunStatus.RETRYING,
        },
        can_retry=(
            record.manual_retry_count < 3
            and record.status
            in {
                AgentRunStatus.FAILED,
                AgentRunStatus.CANCELLED,
                AgentRunStatus.TIMED_OUT,
            }
        ),
    )


@router.get("/capabilities", response_model=AgentOSCapabilitiesResponse)
async def read_agent_os_capabilities(
    request: Request,
    _current_user: Annotated[User, Depends(get_current_user)],
) -> AgentOSCapabilitiesResponse:
    manager = _manager(request)
    policy = manager.orchestrator.policy
    registered = set(manager.orchestrator.registered_specialists)
    return AgentOSCapabilitiesResponse(
        profiles=[
            AgentProfileResponse(
                kind=profile.kind,
                permissions=sorted(profile.permissions, key=lambda item: item.value),
                registered=profile.kind in registered,
            )
            for profile in policy.profiles
        ],
        active_runs=manager.active_count,
        persistence=manager.persistence,
    )


@router.post(
    "/runs",
    response_model=AgentRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_agent_run(
    payload: AgentRunCreateRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
) -> AgentRunResponse:
    try:
        run_request = AgentRunRequest(
            goal=payload.goal,
            task=payload.task,
            source=payload.source,
            specialist=payload.specialist,
            permissions=frozenset({AgentPermission.MODEL_INFERENCE}),
            max_retries=payload.max_retries,
            deadline_seconds=payload.deadline_seconds,
            required_context_tokens=payload.required_context_tokens,
            require_objective_evidence=payload.require_objective_evidence,
            require_owner_approval=payload.require_owner_approval,
        )
        record = await _manager(request).submit(current_user.id, run_request)
    except RuntimeError as exc:
        if str(exc) != "agent run retention is full":
            raise
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Agent run retention is full",
        ) from None
    return _response(record)


@router.get("/runs/{run_id}/events")
async def stream_agent_run_events(
    run_id: UUID,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
    after: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> StreamingResponse:
    manager = _manager(request)
    if await manager.get_for_owner(current_user.id, run_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent run not found",
        )

    async def events():
        sequence = after
        deadline = asyncio.get_running_loop().time() + 30.0
        while asyncio.get_running_loop().time() < deadline:
            if await request.is_disconnected():
                return
            record = await manager.get_for_owner(current_user.id, run_id)
            if record is None:
                return
            pending = [event for event in record.events if event.sequence > sequence]
            for event in pending:
                payload = AgentRunEventResponse(
                    sequence=event.sequence,
                    status=event.status,
                    created_at=event.created_at,
                    step_id=event.step_id,
                    attempt=event.attempt,
                    agent=event.agent,
                    model_id=event.model_id,
                    action=event.action,
                    detail_sha256=event.detail_sha256,
                )
                sequence = event.sequence
                yield (
                    f"id: {event.sequence}\n"
                    "event: mission-status\n"
                    f"data: {payload.model_dump_json()}\n\n"
                )
            if record.status in {
                AgentRunStatus.NEEDS_APPROVAL,
                AgentRunStatus.PAUSED,
                AgentRunStatus.COMPLETED,
                AgentRunStatus.FAILED,
                AgentRunStatus.CANCELLED,
                AgentRunStatus.TIMED_OUT,
            }:
                return
            if not pending:
                await asyncio.sleep(0.2)
        yield ": reconnect\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runs", response_model=AgentRunPageResponse)
async def list_agent_runs(
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> AgentRunPageResponse:
    records = await _manager(request).list_for_owner(current_user.id, limit=limit)
    return AgentRunPageResponse(items=[_response(record) for record in records])


@router.get("/runs/{run_id}", response_model=AgentRunResponse)
async def get_agent_run(
    run_id: UUID,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
) -> AgentRunResponse:
    record = await _manager(request).get_for_owner(current_user.id, run_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent run not found",
        )
    return _response(record)


def _control_error(exc: RuntimeError) -> HTTPException:
    if isinstance(exc, AgentRunNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent run not found",
        )
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.post("/runs/{run_id}/pause", response_model=AgentRunResponse)
async def pause_agent_run(
    run_id: UUID,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
) -> AgentRunResponse:
    try:
        return _response(
            await _manager(request).pause_for_owner(current_user.id, run_id)
        )
    except (AgentRunNotFoundError, AgentRunConflictError) as exc:
        raise _control_error(exc) from None


@router.post("/runs/{run_id}/resume", response_model=AgentRunResponse)
async def resume_agent_run(
    run_id: UUID,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
) -> AgentRunResponse:
    try:
        return _response(
            await _manager(request).resume_for_owner(current_user.id, run_id)
        )
    except (AgentRunNotFoundError, AgentRunConflictError) as exc:
        raise _control_error(exc) from None


@router.post("/runs/{run_id}/approve", response_model=AgentRunResponse)
async def approve_agent_run(
    run_id: UUID,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
) -> AgentRunResponse:
    try:
        return _response(
            await _manager(request).approve_for_owner(current_user.id, run_id)
        )
    except (AgentRunNotFoundError, AgentRunConflictError) as exc:
        raise _control_error(exc) from None


@router.post("/runs/{run_id}/modify", response_model=AgentRunResponse)
async def modify_agent_run(
    run_id: UUID,
    payload: AgentRunModifyRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
) -> AgentRunResponse:
    try:
        return _response(
            await _manager(request).modify_for_owner(current_user.id, run_id, payload.goal)
        )
    except (AgentRunNotFoundError, AgentRunConflictError) as exc:
        raise _control_error(exc) from None


@router.post("/runs/{run_id}/retry", response_model=AgentRunResponse)
async def retry_agent_run(
    run_id: UUID,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
) -> AgentRunResponse:
    try:
        return _response(
            await _manager(request).retry_for_owner(current_user.id, run_id)
        )
    except (AgentRunNotFoundError, AgentRunConflictError) as exc:
        raise _control_error(exc) from None


@router.post("/runs/{run_id}/cancel", response_model=AgentRunResponse)
async def cancel_agent_run(
    run_id: UUID,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
) -> AgentRunResponse:
    try:
        record = await _manager(request).cancel_for_owner(current_user.id, run_id)
    except AgentRunNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent run not found",
        ) from None
    return _response(record)
