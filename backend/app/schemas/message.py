from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.message import MessageRole


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    role: MessageRole
    content: str
    sequence_number: int
    created_at: datetime
    updated_at: datetime


class MessagePageResponse(BaseModel):
    items: list[MessageResponse]
    next_cursor: int | None
