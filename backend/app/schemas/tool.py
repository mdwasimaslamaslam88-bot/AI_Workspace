from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.tool import ToolExecutionStatus


class ToolExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arguments: dict[str, Any]
    conversation_id: UUID | None = None


class ToolDescriptorResponse(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    permission: str
    timeout_seconds: float
    max_output_characters: int


class ToolDescriptorPageResponse(BaseModel):
    items: list[ToolDescriptorResponse]


class ToolExecutionResponse(BaseModel):
    id: UUID
    conversation_id: UUID | None
    tool_name: str
    permission: str
    status: ToolExecutionStatus
    initiator: Literal["explicit_user", "workflow"]
    arguments: dict[str, Any]
    result: Any | None
    error_code: str | None
    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None


class ToolExecutionPageResponse(BaseModel):
    items: list[ToolExecutionResponse]


class ToolExecutionListQuery(BaseModel):
    limit: int = Field(default=50, ge=1, le=100)
