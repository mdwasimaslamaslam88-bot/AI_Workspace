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


class ConversationStateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_pinned: bool | None = None
    is_archived: bool | None = None

    @model_validator(mode="after")
    def require_state_field(self):
        if self.is_pinned is None and self.is_archived is None:
            raise ValueError("at least one conversation state field is required")
        return self


class ConversationForkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    through_sequence_number: int | None = Field(
        default=None,
        ge=1,
        strict=True,
    )
    replacement_content: str | None = Field(
        default=None,
        max_length=100_000,
        pattern=r"\S",
    )

    @model_validator(mode="after")
    def validate_replacement_branch(self):
        if (
            self.replacement_content is not None
            and self.through_sequence_number is None
        ):
            raise ValueError(
                "replacement_content requires through_sequence_number"
            )
        return self


class ConversationCreateResponse(BaseModel):
    id: UUID
    title: str | None
    is_pinned: bool
    is_archived: bool
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
    include_archived: bool = False

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
    is_pinned: bool
    is_archived: bool
    created_at: datetime
    updated_at: datetime


class ConversationCursorResponse(BaseModel):
    updated_at: datetime
    id: UUID


class ConversationPageResponse(BaseModel):
    items: list[ConversationSummaryResponse]
    next_cursor: ConversationCursorResponse | None
