import math
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

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
