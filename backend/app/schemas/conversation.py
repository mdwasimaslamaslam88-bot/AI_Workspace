from datetime import datetime
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from app.repositories.conversation import (
    DEFAULT_CONVERSATION_PAGE_SIZE,
    MAX_CONVERSATION_PAGE_SIZE,
)
from app.schemas.message import MessageResponse


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=255, pattern=r"\S")
    system_prompt: str | None = Field(default=None, pattern=r"\S")
    initial_message: str = Field(pattern=r"\S")
    attachment_ids: list[UUID] = Field(default_factory=list)

    @field_validator("attachment_ids")
    @classmethod
    def require_unique_attachment_ids(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("attachment_ids must be unique")
        return value


class ConversationRename(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(max_length=255, pattern=r"\S")


class ConversationCreateResponse(BaseModel):
    id: UUID
    title: str | None
    created_at: datetime
    updated_at: datetime
    initial_message: MessageResponse


class ConversationListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(
        default=DEFAULT_CONVERSATION_PAGE_SIZE,
        ge=1,
        le=MAX_CONVERSATION_PAGE_SIZE,
    )
    cursor_updated_at: AwareDatetime | None = None
    cursor_id: UUID | None = None

    @model_validator(mode="after")
    def validate_composite_cursor(self):
        if (self.cursor_updated_at is None) != (self.cursor_id is None):
            raise PydanticCustomError(
                "composite_cursor",
                "cursor_updated_at and cursor_id must be provided together"
            )
        return self


class ConversationSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str | None
    created_at: datetime
    updated_at: datetime


class ConversationCursorResponse(BaseModel):
    updated_at: datetime
    id: UUID


class ConversationPageResponse(BaseModel):
    items: list[ConversationSummaryResponse]
    next_cursor: ConversationCursorResponse | None
