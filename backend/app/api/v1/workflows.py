from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.dependencies import get_db_session
from app.models.user import User
from app.schemas.workflow import (
    WorkflowCreateRequest,
    WorkflowListQuery,
    WorkflowPageResponse,
    WorkflowResponse,
)
from app.services.workflow import (
    WorkflowInputInvalidError,
    WorkflowNotFoundError,
    WorkflowNotStartableError,
    WorkflowRunner,
    WorkflowService,
    WorkflowStepDraft,
)

router = APIRouter(prefix="/workflows", tags=["Workflows"])


def _runner(request: Request) -> WorkflowRunner:
    runner = getattr(request.app.state, "workflow_runner", None)
    if runner is None:
        raise RuntimeError("Workflow execution is not configured")
    return runner


@router.get("", response_model=WorkflowPageResponse)
async def list_workflows(
    query: Annotated[WorkflowListQuery, Query()],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> WorkflowPageResponse:
    records = await WorkflowService(session).list_for_owner(
        current_user.id, limit=query.limit
    )
    return WorkflowPageResponse(
        items=[
            WorkflowResponse.model_validate(record, from_attributes=True)
            for record in records
        ]
    )


@router.post(
    "",
    response_model=WorkflowResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_workflow(
    body: WorkflowCreateRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> WorkflowResponse:
    try:
        record = await WorkflowService(session).create_for_owner(
            current_user.id,
            body.name,
            tuple(
                WorkflowStepDraft(step.tool_name, step.arguments)
                for step in body.steps
            ),
        )
    except WorkflowInputInvalidError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Workflow definition is invalid",
        ) from None
    return WorkflowResponse.model_validate(record, from_attributes=True)


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(
    workflow_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> WorkflowResponse:
    record = await WorkflowService(session).get_for_owner(
        current_user.id, workflow_id
    )
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )
    return WorkflowResponse.model_validate(record, from_attributes=True)


@router.post(
    "/{workflow_id}/start",
    response_model=WorkflowResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_workflow(
    workflow_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> WorkflowResponse:
    owner_id = current_user.id
    await session.rollback()
    try:
        record = await _runner(request).start_for_owner(
            owner_id, workflow_id
        )
    except WorkflowNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        ) from None
    except WorkflowNotStartableError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Workflow cannot be started",
        ) from None
    return WorkflowResponse.model_validate(record, from_attributes=True)


@router.delete("/{workflow_id}", response_model=WorkflowResponse)
async def cancel_workflow(
    workflow_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> WorkflowResponse:
    owner_id = current_user.id
    await session.rollback()
    try:
        record = await _runner(request).cancel_for_owner(
            owner_id, workflow_id
        )
    except WorkflowNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        ) from None
    return WorkflowResponse.model_validate(record, from_attributes=True)
