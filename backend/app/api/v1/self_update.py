from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies import get_current_user
from app.maintenance import SelfUpdateError, SelfUpdateManager, UpdateState, UpdateStatus
from app.models.user import User
from app.schemas.self_update import (
    SelfUpdateDecisionRequest,
    SelfUpdateStatusResponse,
    UpdateGateResponse,
)


router = APIRouter(prefix="/updates", tags=["Updates"])


def _manager(request: Request) -> SelfUpdateManager | None:
    manager = getattr(request.app.state, "self_update_manager", None)
    return manager if isinstance(manager, SelfUpdateManager) else None


def _response(manager: SelfUpdateManager | None, state: UpdateState) -> SelfUpdateStatusResponse:
    checkpoint_ready = bool(
        manager is not None
        and state.checkpoint_id is not None
        and state.status
        in {
            UpdateStatus.READY,
            UpdateStatus.ACTIVATED,
            UpdateStatus.ROLLED_BACK,
            UpdateStatus.CANCELLED,
        }
    )
    return SelfUpdateStatusResponse(
        configured=manager is not None,
        status=state.status,
        version=state.version,
        candidate_commit=state.candidate_commit,
        checkpoint_ready=checkpoint_ready,
        rollback_ready=checkpoint_ready,
        activation_requires_owner=state.status is UpdateStatus.READY,
        gates=[UpdateGateResponse(name=gate.name, passed=gate.passed) for gate in state.gate_results],
        failure_code=state.failure_code,
    )


@router.get("/status", response_model=SelfUpdateStatusResponse)
async def read_update_status(
    request: Request,
    _current_user: Annotated[User, Depends(get_current_user)],
) -> SelfUpdateStatusResponse:
    manager = _manager(request)
    if manager is None:
        return _response(None, UpdateState())
    try:
        return _response(manager, manager.state())
    except SelfUpdateError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Self-update state is unavailable.",
        ) from exc


@router.post("/decision", response_model=SelfUpdateStatusResponse)
async def decide_update(
    body: SelfUpdateDecisionRequest,
    request: Request,
    _current_user: Annotated[User, Depends(get_current_user)],
) -> SelfUpdateStatusResponse:
    manager = _manager(request)
    if manager is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Self-update is not configured.",
        )
    try:
        state = (
            manager.activate_ready(user_confirmed=True)
            if body.decision == "update"
            else manager.cancel_ready()
        )
    except SelfUpdateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No validated update is ready for that decision.",
        ) from exc
    return _response(manager, state)
