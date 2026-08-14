from pydantic import BaseModel, ConfigDict, Field

from app.ai.catalog import ModelAvailability, ModelCapability, ModelModality
from app.schemas.message import MessageResponse


class LocalModelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str
    display_name: str
    runtime_id: str
    modality: ModelModality
    family: str | None
    parameter_class: str | None
    capabilities: list[ModelCapability]
    context_window: int | None
    quantization: str | None
    estimated_vram_bytes: int | None
    availability: ModelAvailability


class LocalModelPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[LocalModelResponse]


class ConversationTextGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(
        pattern=r"^[a-z0-9][a-z0-9_-]{0,63}:[a-f0-9]{24}$"
    )
    user_message: str | None = Field(default=None, pattern=r"\S")
    max_output_tokens: int = Field(default=1024, strict=True, ge=1, le=1024)


class ConversationTextGenerationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str
    message: MessageResponse
