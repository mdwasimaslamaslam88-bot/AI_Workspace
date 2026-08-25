import math
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from app.ai.catalog import (
    ModelAvailability,
    ModelCapability,
    ModelModality,
    ModelScaleClass,
)
from app.ai.routing import ModelTask
from app.hardware import HardwareClass
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
    scale_class: ModelScaleClass | None
    required_vram_bytes: int | None
    required_ram_bytes: int | None
    installed: bool
    runnable_now: bool
    future_capable: bool
    hardware_class: HardwareClass | None
    fallback_model_id: str | None


class LocalModelPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[LocalModelResponse]


ProductCapabilityId = Literal[
    "chat",
    "vision_input",
    "attachments",
    "documents_rag",
    "personal_memory",
    "bounded_tools",
    "bounded_workflows",
    "image_generation",
    "image_editing",
    "voice_input",
    "voice_output",
]
ProductCapabilityReason = Literal[
    "asset_storage_required",
    "local_model_runtime_unavailable",
    "allowlisted_text_model_required",
    "allowlisted_vision_model_required",
    "local_image_runtime_and_model_required",
    "local_image_edit_runtime_and_model_required",
    "local_voice_runtime_and_models_required",
]
PRODUCT_CAPABILITY_IDS = frozenset(
    {
        "chat",
        "vision_input",
        "attachments",
        "documents_rag",
        "personal_memory",
        "bounded_tools",
        "bounded_workflows",
        "image_generation",
        "image_editing",
        "voice_input",
        "voice_output",
    }
)


class ProductCapabilityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ProductCapabilityId
    status: Literal["available", "unavailable"]
    blocking_reasons: list[ProductCapabilityReason] = Field(max_length=3)

    @model_validator(mode="after")
    def require_consistent_status(self):
        if self.status == "available" and self.blocking_reasons:
            raise ValueError("available capabilities cannot have blockers")
        if self.status == "unavailable" and not self.blocking_reasons:
            raise ValueError("unavailable capabilities require a blocker")
        if len(self.blocking_reasons) != len(set(self.blocking_reasons)):
            raise ValueError("capability blockers must be unique")
        return self


class ProductCapabilityPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ProductCapabilityResponse] = Field(min_length=11, max_length=11)

    @model_validator(mode="after")
    def require_exact_capability_set(self):
        if {item.id for item in self.items} != PRODUCT_CAPABILITY_IDS:
            raise ValueError("product capability set is invalid")
        return self


class ConversationTextGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9_-]{0,63}:[a-f0-9]{24}$"
    )
    task: ModelTask | None = None
    user_message: str | None = Field(default=None, pattern=r"\S")
    attachment_ids: list[UUID] = Field(default_factory=list)
    max_output_tokens: int = Field(default=1024, strict=True, ge=1, le=1024)
    temperature: float | None = Field(
        default=None,
        strict=True,
        ge=0.0,
        le=2.0,
        allow_inf_nan=False,
    )
    seed: int | None = Field(
        default=None,
        strict=True,
        ge=0,
        le=2_147_483_647,
    )
    top_p: float | None = Field(
        default=None,
        strict=True,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    top_k: int | None = Field(
        default=None,
        strict=True,
        ge=1,
        le=100,
    )
    min_p: float | None = Field(
        default=None,
        strict=True,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    repeat_penalty: float | None = Field(
        default=None,
        strict=True,
        ge=0.5,
        le=2.0,
        allow_inf_nan=False,
    )
    repeat_last_n: int | None = Field(
        default=None,
        strict=True,
        ge=0,
        le=2048,
    )
    typical_p: float | None = Field(
        default=None,
        strict=True,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )
    presence_penalty: float | None = Field(
        default=None,
        strict=True,
        ge=-2.0,
        le=2.0,
        allow_inf_nan=False,
    )
    frequency_penalty: float | None = Field(
        default=None,
        strict=True,
        ge=-2.0,
        le=2.0,
        allow_inf_nan=False,
    )
    stop_sequences: list[
        Annotated[
            str,
            Field(strict=True, min_length=1, max_length=128),
        ]
    ] | None = Field(default=None, min_length=1, max_length=4)

    @field_validator("attachment_ids")
    @classmethod
    def require_unique_attachment_ids(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("attachment_ids must be unique")
        return value

    @model_validator(mode="after")
    def require_user_message_for_attachments(self):
        if self.model_id is None and self.task is None:
            raise PydanticCustomError(
                "missing_model_selection",
                "model_id or task is required",
            )
        if self.attachment_ids and self.user_message is None:
            raise PydanticCustomError(
                "attachments_without_user_message",
                "attachment_ids require user_message",
            )
        return self

    @field_validator("temperature", mode="before")
    @classmethod
    def validate_temperature_input(cls, value):
        if value is None:
            raise PydanticCustomError(
                "temperature_null",
                "temperature must not be null",
            )
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                is_finite_temperature = math.isfinite(value)
            except OverflowError:
                is_finite_temperature = False
            if not is_finite_temperature:
                # Keep the existing public validation error JSON-serializable.
                return "non-finite temperature"
        return value

    @field_validator("seed", mode="before")
    @classmethod
    def validate_seed_input(cls, value):
        if value is None:
            raise PydanticCustomError(
                "seed_null",
                "seed must not be null",
            )
        if isinstance(value, float) and not math.isfinite(value):
            # Keep the existing public validation error JSON-serializable.
            return "non-finite seed"
        return value

    @field_validator("top_p", mode="before")
    @classmethod
    def validate_top_p_input(cls, value):
        if value is None:
            raise PydanticCustomError(
                "top_p_null",
                "top_p must not be null",
            )
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                is_finite_top_p = math.isfinite(value)
            except OverflowError:
                is_finite_top_p = False
            if not is_finite_top_p:
                # Keep the existing public validation error JSON-serializable.
                return "non-finite top_p"
        return value

    @field_validator("top_k", mode="before")
    @classmethod
    def validate_top_k_input(cls, value):
        if value is None:
            raise PydanticCustomError(
                "top_k_null",
                "top_k must not be null",
            )
        if isinstance(value, float) and not math.isfinite(value):
            # Keep the existing public validation error JSON-serializable.
            return "non-finite top_k"
        return value

    @field_validator("min_p", mode="before")
    @classmethod
    def validate_min_p_input(cls, value):
        if value is None:
            raise PydanticCustomError(
                "min_p_null",
                "min_p must not be null",
            )
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                is_finite_min_p = math.isfinite(value)
            except OverflowError:
                is_finite_min_p = False
            if not is_finite_min_p:
                # Keep the existing public validation error JSON-serializable.
                return "non-finite min_p"
        return value

    @field_validator("repeat_penalty", mode="before")
    @classmethod
    def validate_repeat_penalty_input(cls, value):
        if value is None:
            raise PydanticCustomError(
                "repeat_penalty_null",
                "repeat_penalty must not be null",
            )
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                is_finite_repeat_penalty = math.isfinite(value)
            except OverflowError:
                is_finite_repeat_penalty = False
            if not is_finite_repeat_penalty:
                # Keep the existing public validation error JSON-serializable.
                return "non-finite repeat_penalty"
        return value

    @field_validator("repeat_last_n", mode="before")
    @classmethod
    def validate_repeat_last_n_input(cls, value):
        if value is None:
            raise PydanticCustomError(
                "repeat_last_n_null",
                "repeat_last_n must not be null",
            )
        if isinstance(value, float) and not math.isfinite(value):
            # Keep the existing public validation error JSON-serializable.
            return "non-finite repeat_last_n"
        return value

    @field_validator("typical_p", mode="before")
    @classmethod
    def validate_typical_p_input(cls, value):
        if value is None:
            raise PydanticCustomError(
                "typical_p_null",
                "typical_p must not be null",
            )
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                is_finite_typical_p = math.isfinite(value)
            except OverflowError:
                is_finite_typical_p = False
            if not is_finite_typical_p:
                # Keep the existing public validation error JSON-serializable.
                return "non-finite typical_p"
        return value

    @field_validator("presence_penalty", mode="before")
    @classmethod
    def validate_presence_penalty_input(cls, value):
        if value is None:
            raise PydanticCustomError(
                "presence_penalty_null",
                "presence_penalty must not be null",
            )
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                is_finite_presence_penalty = math.isfinite(value)
            except OverflowError:
                is_finite_presence_penalty = False
            if not is_finite_presence_penalty:
                # Keep the existing public validation error JSON-serializable.
                return "non-finite presence_penalty"
        return value

    @field_validator("frequency_penalty", mode="before")
    @classmethod
    def validate_frequency_penalty_input(cls, value):
        if value is None:
            raise PydanticCustomError(
                "frequency_penalty_null",
                "frequency_penalty must not be null",
            )
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                is_finite_frequency_penalty = math.isfinite(value)
            except OverflowError:
                is_finite_frequency_penalty = False
            if not is_finite_frequency_penalty:
                # Keep the existing public validation error JSON-serializable.
                return "non-finite frequency_penalty"
        return value

    @field_validator("stop_sequences", mode="before")
    @classmethod
    def validate_stop_sequences_input(cls, value):
        if value is None:
            raise PydanticCustomError(
                "stop_sequences_null",
                "stop_sequences must not be null",
            )
        return value


class ConversationTextGenerationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str
    message: MessageResponse
