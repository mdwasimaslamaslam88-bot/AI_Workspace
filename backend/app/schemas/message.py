from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.message import MessageRole


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(pattern=r"\S")
    attachment_ids: list[UUID] = Field(default_factory=list)

    @field_validator("attachment_ids")
    @classmethod
    def require_unique_attachment_ids(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("attachment_ids must be unique")
        return value


class MessageAttachmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(validation_alias="asset_id")
    position: int
    state: Literal["active", "deleted"]
    original_filename: str | None
    media_type: str | None
    byte_size: int | None


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    role: MessageRole
    content: str
    sequence_number: int
    created_at: datetime
    updated_at: datetime
    attachments: list[MessageAttachmentResponse] = Field(
        default_factory=list,
        validation_alias="asset_links",
    )


class MessagePageResponse(BaseModel):
    items: list[MessageResponse]
    next_cursor: int | None
