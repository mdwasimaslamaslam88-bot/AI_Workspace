from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    hardware_class: HardwareClass
    status: Literal["ready"] = "ready"


class SystemDiagnosticsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["local", "remote"]
    services: list[ServiceDiagnosticResponse] = Field(min_length=11, max_length=11)
    gpus: list[GpuDiagnosticResponse] = Field(max_length=16)

    @model_validator(mode="after")
    def require_exact_service_set(self):
        if {service.id for service in self.services} != DIAGNOSTIC_SERVICE_IDS:
            raise ValueError("diagnostic service set is invalid")
        return self
