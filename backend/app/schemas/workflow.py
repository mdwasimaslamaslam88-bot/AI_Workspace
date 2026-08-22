from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.workflow import WorkflowStatus


class WorkflowStepCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any]


class WorkflowCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    steps: list[WorkflowStepCreateRequest] = Field(min_length=1, max_length=8)


class WorkflowStepResponse(BaseModel):
    id: UUID
    position: int
    tool_name: str
    permission: str
    arguments: dict[str, Any]
    status: WorkflowStatus
    tool_execution_id: UUID | None
    result: Any | None
    error_code: str | None
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None


class WorkflowResponse(BaseModel):
    id: UUID
    name: str | None
    status: WorkflowStatus
    step_count: int
    current_step_position: int | None
    cancel_requested: bool
    result: Any | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    steps: list[WorkflowStepResponse]


class WorkflowPageResponse(BaseModel):
    items: list[WorkflowResponse]


class WorkflowListQuery(BaseModel):
    limit: int = Field(default=20, ge=1, le=50)
