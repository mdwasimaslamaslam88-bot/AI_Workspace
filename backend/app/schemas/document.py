from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.document import DocumentStatus


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    asset_id: UUID
    status: DocumentStatus
    source_state: Literal["active", "deleted"]
    original_filename: str | None
    media_type: str | None
    chunk_count: int
    character_count: int
    failure_code: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class DocumentPageResponse(BaseModel):
    items: list[DocumentResponse]


class DocumentSearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=10_000, pattern=r"\S")
    limit: int = Field(default=4, ge=1, le=4)


class DocumentSearchResultResponse(BaseModel):
    chunk_id: UUID
    asset_id: UUID
    content: str
    score: float
    original_filename: str | None
    provenance_kind: str
    page_number: int | None
    row_start: int | None
    row_end: int | None
    section: str | None


class DocumentSearchResponse(BaseModel):
    items: list[DocumentSearchResultResponse]
