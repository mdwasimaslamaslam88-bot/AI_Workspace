from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.creative import CreativeExperienceMode, CreativeExperienceStatus


_TEXT_PATTERN = r"^\S(?:[\s\S]*\S)?$"
_LANGUAGE_PATTERN = r"^[A-Za-z][A-Za-z0-9-]{1,34}$"


class CreativeExperienceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: CreativeExperienceMode
    title: str = Field(min_length=1, max_length=160, pattern=_TEXT_PATTERN)
    premise: str = Field(min_length=1, max_length=4_000, pattern=_TEXT_PATTERN)
    genre: str = Field(min_length=1, max_length=80, pattern=_TEXT_PATTERN)
    language: str = Field(pattern=_LANGUAGE_PATTERN)
    character_name: str | None = Field(
        default=None, min_length=1, max_length=120, pattern=_TEXT_PATTERN
    )

    @model_validator(mode="after")
    def character_mode_requires_name(self):
        if self.mode is CreativeExperienceMode.CHARACTER and self.character_name is None:
            raise ValueError("fictional character mode requires a name")
        return self


class CreativeTurnCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_input: str = Field(min_length=1, max_length=4_000, pattern=_TEXT_PATTERN)


class CreativeTurnResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    position: int
    owner_input: str
    output: str
    output_sha256: str
    model_id: str
    created_at: datetime


class CreativeExperienceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    mode: CreativeExperienceMode
    title: str
    premise: str
    genre: str
    language: str
    character_name: str | None
    safety_tier: Literal["general"]
    status: CreativeExperienceStatus
    turn_count: int
    turns: list[CreativeTurnResponse]
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class CreativeExperiencePageResponse(BaseModel):
    items: list[CreativeExperienceResponse]


class CreativeCapabilitiesResponse(BaseModel):
    interactive_stories: bool = True
    text_games: bool = True
    fictional_characters: bool = True
    verified_local_text_generation: bool = True
    general_audience_only: bool = True
    image_generation_status: Literal["runtime_dependent"] = "runtime_dependent"
    voice_status: Literal["runtime_dependent"] = "runtime_dependent"
    video_generation_status: Literal["external_dependency"] = "external_dependency"
    animation_status: Literal["external_dependency"] = "external_dependency"
    audio_generation_editing_status: Literal["external_dependency"] = "external_dependency"
    adult_experience_status: Literal["external_dependency"] = "external_dependency"
    external_dependencies: list[str] = Field(
        default_factory=lambda: [
            "verified_video_animation_or_audio_runtime",
            "jurisdiction_check",
            "age_verification",
            "consent_policy",
        ]
    )
