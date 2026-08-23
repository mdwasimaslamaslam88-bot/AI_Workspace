import math
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.asset import AssetResponse
from app.schemas.message import MessageResponse


_MODEL_ID_PATTERN = r"[a-z0-9][a-z0-9_-]{0,63}:[a-f0-9]{24}"


class ImageGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID
    model_id: str = Field(pattern=_MODEL_ID_PATTERN)
    prompt: str = Field(min_length=1, max_length=2_000)
    negative_prompt: str = Field(default="", max_length=1_000)
    width: int = Field(default=768, ge=512, le=1_024)
    height: int = Field(default=768, ge=512, le=1_024)
    steps: int = Field(default=20, ge=1, le=30)
    guidance: float = Field(default=7.0, ge=1.0, le=10.0)
    seed: int = Field(default=0, ge=0, le=9_007_199_254_740_991)

    @model_validator(mode="after")
    def require_bounded_dimensions(self) -> Self:
        if self.width % 64 or self.height % 64:
            raise ValueError("image dimensions must be multiples of 64")
        if self.width * self.height > 1_048_576:
            raise ValueError("image pixel count is too large")
        if not math.isfinite(self.guidance):
            raise ValueError("image guidance must be finite")
        return self


class ImageEditingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID
    model_id: str = Field(pattern=_MODEL_ID_PATTERN)
    source_asset_id: UUID
    mask_asset_id: UUID | None = None
    instruction: str = Field(min_length=1, max_length=2_000)
    negative_prompt: str = Field(default="", max_length=1_000)
    steps: int = Field(default=20, ge=1, le=30)
    guidance: float = Field(default=7.0, ge=1.0, le=10.0)
    denoise: float = Field(default=0.65, ge=0.1, le=1.0)
    seed: int = Field(default=0, ge=0, le=9_007_199_254_740_991)

    @model_validator(mode="after")
    def require_finite_options(self) -> Self:
        if not math.isfinite(self.guidance) or not math.isfinite(self.denoise):
            raise ValueError("image edit options must be finite")
        if self.mask_asset_id == self.source_asset_id:
            raise ValueError("image edit mask must be a distinct asset")
        return self


class ImageOperationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset: AssetResponse
    message: MessageResponse
    created: bool
