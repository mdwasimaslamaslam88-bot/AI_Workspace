from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.ai.routing import ModelTask
from app.external_ai.contracts import ExternalProviderKind, ExternalProviderStatus


class ExternalAIGlobalUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class ExternalModelPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
    tasks: list[ModelTask] = Field(min_length=1, max_length=16)
    verified: bool = False
    verification_evidence_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    measured_quality: float = Field(default=0.0, ge=0, le=100)
    measured_latency_ms: float = Field(default=0.0, ge=0, le=3_600_000)
    stability_rate: float = Field(default=0.0, ge=0, le=1)
    context_window: int = Field(default=0, strict=True, ge=0, le=10_000_000)
    input_cost_micros_per_million_tokens: int = Field(default=0, strict=True, ge=0, le=10_000_000_000)
    output_cost_micros_per_million_tokens: int = Field(default=0, strict=True, ge=0, le=10_000_000_000)

    @field_validator("tasks")
    @classmethod
    def require_unique_tasks(cls, value: list[ModelTask]) -> list[ModelTask]:
        if len(value) != len(set(value)):
            raise ValueError("external model tasks must be unique")
        return value


class ExternalProviderUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ExternalProviderKind
    api_key: SecretStr | None = Field(default=None, min_length=16, max_length=512)
    enabled: bool = False
    free_tier: bool = False
    priority: int = Field(default=100, strict=True, ge=0, le=1_000)
    timeout_seconds: float = Field(default=30.0, ge=1, le=60)
    rate_limit_requests_per_minute: int = Field(default=30, strict=True, ge=1, le=1_000)
    spending_limit_micros: int = Field(default=0, strict=True, ge=0, le=10**15)
    quota_remaining_tokens: int | None = Field(default=None, strict=True, ge=0, le=10**15)
    models: list[ExternalModelPolicyRequest] = Field(default_factory=list, max_length=64)

    @field_validator("models")
    @classmethod
    def require_unique_models(cls, value: list[ExternalModelPolicyRequest]) -> list[ExternalModelPolicyRequest]:
        if len({model.model_id for model in value}) != len(value):
            raise ValueError("external provider models must be unique")
        return value


class ExternalModelPolicyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str
    tasks: list[ModelTask]
    verified: bool
    verification_evidence_sha256: str | None
    measured_quality: float
    measured_latency_ms: float
    stability_rate: float
    context_window: int
    input_cost_micros_per_million_tokens: int
    output_cost_micros_per_million_tokens: int


class ExternalProviderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str
    kind: ExternalProviderKind
    enabled: bool
    key_configured: bool
    free_tier: bool
    priority: int
    timeout_seconds: float
    rate_limit_requests_per_minute: int
    spending_limit_micros: int
    spent_micros: int
    quota_remaining_tokens: int | None
    status: ExternalProviderStatus
    models: list[ExternalModelPolicyResponse] = Field(max_length=64)


class ExternalAISettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configured: bool
    global_enabled: bool
    providers: list[ExternalProviderResponse] = Field(max_length=16)
    supported_provider_kinds: list[ExternalProviderKind] = Field(min_length=3, max_length=3)


class ExternalProviderHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str
    status: ExternalProviderStatus


class ExternalProviderDiscoveryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str
    discovered_model_ids: list[str] = Field(max_length=512)
    production_admitted: bool = False
