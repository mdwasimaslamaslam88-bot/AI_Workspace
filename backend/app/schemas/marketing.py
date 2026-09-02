from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.marketing import (
    MarketingCampaignStatus,
    MarketingStageKind,
    MarketingStageStatus,
)


class MarketingSourceFactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_reference: str = Field(
        min_length=1, max_length=512, pattern=r"^\S(?:[\s\S]*\S)?$"
    )
    fact: str = Field(
        min_length=1, max_length=2_000, pattern=r"^\S(?:[\s\S]*\S)?$"
    )


class MarketingCampaignCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120, pattern=r"^\S(?:[\s\S]*\S)?$")
    objective: str = Field(
        min_length=1, max_length=2_000, pattern=r"^\S(?:[\s\S]*\S)?$"
    )
    product: str = Field(
        min_length=1, max_length=500, pattern=r"^\S(?:[\s\S]*\S)?$"
    )
    audience: str = Field(
        min_length=1, max_length=1_000, pattern=r"^\S(?:[\s\S]*\S)?$"
    )
    channels: list[Literal["email", "social", "search", "web"]] = Field(
        min_length=1, max_length=4
    )
    source_facts: list[MarketingSourceFactRequest] = Field(
        min_length=1, max_length=16
    )
    publisher_connector_id: UUID | None = None
    publish_path: str | None = Field(default=None, min_length=1, max_length=512)

    @field_validator("channels")
    @classmethod
    def unique_channels(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("marketing channels must be unique")
        return value


class MarketingAnalyticsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_reference: str = Field(
        min_length=1, max_length=512, pattern=r"^\S(?:[\s\S]*\S)?$"
    )
    observed_at: datetime
    impressions: int = Field(strict=True, ge=0, le=10**15)
    clicks: int = Field(strict=True, ge=0, le=10**15)
    conversions: int = Field(strict=True, ge=0, le=10**15)
    spend_minor: int = Field(strict=True, ge=0, le=10**15)
    revenue_minor: int = Field(strict=True, ge=0, le=10**15)

    @field_validator("observed_at")
    @classmethod
    def timestamp_is_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("analytics timestamp requires a timezone")
        return value


class MarketingSourceFactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_reference: str
    fact: str


class MarketingStageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    position: int
    kind: MarketingStageKind
    status: MarketingStageStatus
    output: str | None
    output_sha256: str | None
    model_id: str | None
    connector_execution_id: UUID | None
    error_code: str | None
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None


class MarketingCampaignResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    objective: str
    product: str
    audience: str
    channels: list[str]
    source_facts: list[MarketingSourceFactResponse]
    publisher_connector_id: UUID | None
    publish_path: str | None
    status: MarketingCampaignStatus
    current_stage: MarketingStageKind | None
    analytics: dict[str, Any] | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    approved_at: datetime | None
    published_at: datetime | None
    completed_at: datetime | None
    stages: list[MarketingStageResponse]


class MarketingCampaignPageResponse(BaseModel):
    items: list[MarketingCampaignResponse] = Field(max_length=50)
