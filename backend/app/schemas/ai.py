from pydantic import BaseModel, ConfigDict

from app.ai.catalog import ModelAvailability, ModelCapability, ModelModality


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
