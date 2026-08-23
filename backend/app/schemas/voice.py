from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.audio import MAX_AUDIO_DURATION_SECONDS, MAX_TRANSCRIPT_CHARACTERS
from app.schemas.asset import AssetResponse


_MODEL_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}:[a-f0-9]{24}$"


class VoiceTranscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: UUID
    model_id: str = Field(pattern=_MODEL_ID_PATTERN)


class VoiceTranscriptionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_TRANSCRIPT_CHARACTERS)
    language: str | None = Field(
        default=None,
        min_length=1,
        max_length=16,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    duration_seconds: float = Field(gt=0, le=MAX_AUDIO_DURATION_SECONDS)


class VoiceSynthesisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(pattern=_MODEL_ID_PATTERN)
    text: str = Field(min_length=1, max_length=2_000, pattern=r"\S")


class VoiceSynthesisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset: AssetResponse
    created: bool
