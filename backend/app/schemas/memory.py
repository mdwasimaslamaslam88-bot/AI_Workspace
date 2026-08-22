from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.memory import MAX_MEMORY_CONTENT_CHARACTERS, MemoryCategory


class MemoryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: MemoryCategory
    content: str = Field(min_length=1, max_length=MAX_MEMORY_CONTENT_CHARACTERS)


class MemoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    category: MemoryCategory
    state: Literal["active", "deleted"]
    content: str | None
    provenance_kind: Literal["explicit_user_entry"]
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None

    @model_validator(mode="before")
    @classmethod
    def derive_state(cls, value):
        if isinstance(value, dict):
            return value
        return {
            "id": value.id,
            "category": value.category,
            "state": "deleted" if value.deleted_at is not None else "active",
            "content": value.content,
            "provenance_kind": value.provenance_kind,
            "created_at": value.created_at,
            "updated_at": value.updated_at,
            "deleted_at": value.deleted_at,
        }

    @model_validator(mode="after")
    def validate_tombstone(self):
        if (self.state == "deleted") != (self.content is None):
            raise ValueError("memory state is inconsistent")
        return self


class MemoryPageResponse(BaseModel):
    items: list[MemoryResponse]


class MemorySettingUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class MemorySettingResponse(BaseModel):
    enabled: bool
    created_at: datetime | None
    updated_at: datetime | None


class MemorySearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=10_000, pattern=r"\S")
    limit: int = Field(default=8, ge=1, le=8)


class MemorySearchResultResponse(BaseModel):
    id: UUID
    category: MemoryCategory
    content: str
    score: float
    created_at: datetime


class MemorySearchResponse(BaseModel):
    items: list[MemorySearchResultResponse]
