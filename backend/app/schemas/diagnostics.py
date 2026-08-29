from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ai.admission import (
    ModelAdmissionReason,
    ModelEligibilityStatus,
    PerformanceClass,
)
from app.ai.routing import InferenceMode, ModelTask
from app.hardware import HardwareClass


DiagnosticStatus = Literal["ready", "unavailable", "unconfigured"]
DiagnosticServiceId = Literal[
    "backend",
    "database",
    "redis",
    "ollama",
    "vision",
    "image_runtime",
    "speech_to_text",
    "text_to_speech",
    "storage",
    "remote_gateway",
    "gpu",
]
DIAGNOSTIC_SERVICE_IDS = frozenset(
    {
        "backend",
        "database",
        "redis",
        "ollama",
        "vision",
        "image_runtime",
        "speech_to_text",
        "text_to_speech",
        "storage",
        "remote_gateway",
        "gpu",
    }
)


class ServiceDiagnosticResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: DiagnosticServiceId
    status: DiagnosticStatus


class GpuDiagnosticResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=96)
    vram_bytes: int = Field(strict=True, ge=1)
    free_vram_bytes: int | None = Field(default=None, strict=True, ge=0)
    vendor: str | None = Field(default=None, min_length=1, max_length=32)
    compute_capability: str | None = Field(default=None, min_length=1, max_length=16)
    driver_version: str | None = Field(default=None, min_length=1, max_length=32)
    runtime: str | None = Field(default=None, min_length=1, max_length=32)
    runtime_version: str | None = Field(default=None, min_length=1, max_length=32)
    hardware_class: HardwareClass
    status: Literal["ready"] = "ready"


class HardwareDiagnosticResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile_gib: int = Field(strict=True, ge=0, le=1024)
    gpu_count: int = Field(strict=True, ge=0, le=16)
    total_ram_bytes: int = Field(strict=True, ge=1)
    available_ram_bytes: int | None = Field(default=None, strict=True, ge=0)
    swap_total_bytes: int = Field(strict=True, ge=0)
    swap_free_bytes: int = Field(strict=True, ge=0)
    storage_total_bytes: int | None = Field(default=None, strict=True, ge=0)
    storage_free_bytes: int | None = Field(default=None, strict=True, ge=0)
    cpu_model: str = Field(min_length=1, max_length=160)
    cpu_logical_count: int = Field(strict=True, ge=1, le=65_536)
    os_name: str = Field(min_length=1, max_length=64)
    os_version: str = Field(min_length=1, max_length=128)
    architecture: str = Field(min_length=1, max_length=32)
    upgrade_detected: bool
    capability_cache_invalidated: bool
    restart_required: bool
    runtime_validated: bool


class ModelEligibilityDiagnosticResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}:[a-f0-9]{24}$")
    display_name: str = Field(min_length=1, max_length=255)
    runtime_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    status: ModelEligibilityStatus
    reasons: list[ModelAdmissionReason] = Field(min_length=1, max_length=4)
    performance: PerformanceClass
    verified: bool
    fallback_model_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9_-]{0,63}:[a-f0-9]{24}$",
    )


class ModelRouteDiagnosticResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: ModelTask
    model_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}:[a-f0-9]{24}$")
    fallback_model_ids: list[str] = Field(max_length=32)
    inference_mode: InferenceMode


class SystemDiagnosticsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["local", "remote"]
    services: list[ServiceDiagnosticResponse] = Field(min_length=11, max_length=11)
    gpus: list[GpuDiagnosticResponse] = Field(max_length=16)
    hardware: HardwareDiagnosticResponse | None = None
    models: list[ModelEligibilityDiagnosticResponse] = Field(
        default_factory=list,
        max_length=256,
    )
    routes: list[ModelRouteDiagnosticResponse] = Field(
        default_factory=list,
        max_length=20,
    )

    @model_validator(mode="after")
    def require_exact_service_set(self):
        if {service.id for service in self.services} != DIAGNOSTIC_SERVICE_IDS:
            raise ValueError("diagnostic service set is invalid")
        return self
