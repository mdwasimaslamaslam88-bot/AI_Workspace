from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.hardware import (
    HardwareAdmissionStatus,
    HardwareInventory,
    HardwarePlanner,
    OffloadPolicy,
)


class ModelEligibilityStatus(StrEnum):
    RUNNABLE_NOW = "runnable_now"
    RUNNABLE_WITH_OFFLOAD = "runnable_with_offload"
    FUTURE_CAPABLE = "future_capable"
    HARDWARE_INSUFFICIENT = "hardware_insufficient"
    RUNTIME_INCOMPATIBLE = "runtime_incompatible"
    NOT_INSTALLED = "not_installed"
    DOWNLOAD_REQUIRED = "download_required"
    VERIFICATION_REQUIRED = "verification_required"
    DISABLED = "disabled"


class ModelAdmissionReason(StrEnum):
    ELIGIBLE = "eligible"
    VRAM_INSUFFICIENT = "vram_insufficient"
    RAM_INSUFFICIENT = "ram_insufficient"
    RUNTIME_UNSUPPORTED = "runtime_unsupported"
    COMPUTE_CAPABILITY_UNSUPPORTED = "compute_capability_unsupported"
    MODEL_NOT_INSTALLED = "model_not_installed"
    DOWNLOAD_REQUIRED = "download_required"
    MULTI_GPU_REQUIRED = "multi_gpu_required"
    VERIFICATION_REQUIRED = "verification_required"
    MODEL_DISABLED = "model_disabled"
    METADATA_INCOMPLETE = "metadata_incomplete"
    OFFLOAD_TOO_SLOW = "offload_too_slow"
    MODEL_UNAVAILABLE = "model_unavailable"


class PerformanceClass(StrEnum):
    INTERACTIVE = "interactive"
    ACCEPTABLE = "acceptable"
    SLOW = "slow"
    EXPERIMENTAL = "experimental"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class ModelAdmissionRequest:
    installed: bool
    available: bool
    enabled: bool = True
    verified: bool = True
    download_required: bool = False
    runtime_supported: bool = True
    required_vram_bytes: int | None = None
    minimum_vram_bytes: int | None = None
    required_ram_bytes: int | None = None
    offload_required_ram_bytes: int | None = None
    offload_policy: OffloadPolicy = OffloadPolicy.NONE
    offload_performance: PerformanceClass = PerformanceClass.UNSUPPORTED
    supports_multi_gpu: bool = False
    minimum_gpu_count: int = 1
    minimum_compute_capability: str | None = None


@dataclass(frozen=True, slots=True)
class ModelAdmissionDecision:
    status: ModelEligibilityStatus
    reasons: tuple[ModelAdmissionReason, ...]
    performance: PerformanceClass
    usable_vram_bytes: int
    usable_ram_bytes: int

    @property
    def eligible(self) -> bool:
        return self.status in {
            ModelEligibilityStatus.RUNNABLE_NOW,
            ModelEligibilityStatus.RUNNABLE_WITH_OFFLOAD,
        }


def _compute_tuple(value: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(part) for part in value.split("."))
    except ValueError:
        return None


class ModelAdmissionEngine:
    """Authoritative, fail-closed model admission policy."""

    def __init__(self, inventory: HardwareInventory) -> None:
        self.inventory = inventory
        self.planner = HardwarePlanner(inventory)

    def evaluate(self, request: ModelAdmissionRequest) -> ModelAdmissionDecision:
        if not isinstance(request, ModelAdmissionRequest):
            raise TypeError("request must be a ModelAdmissionRequest")

        usable_ram = max(
            0,
            self.inventory.total_ram_bytes - self.planner.ram_reserve_bytes,
        )
        usable_vram = max(
            0,
            self.inventory.largest_gpu_vram_bytes - self.planner.gpu_reserve_bytes,
        )

        def decision(
            status: ModelEligibilityStatus,
            *reasons: ModelAdmissionReason,
            performance: PerformanceClass = PerformanceClass.UNSUPPORTED,
        ) -> ModelAdmissionDecision:
            return ModelAdmissionDecision(
                status=status,
                reasons=tuple(reasons),
                performance=performance,
                usable_vram_bytes=usable_vram,
                usable_ram_bytes=usable_ram,
            )

        if not request.enabled:
            return decision(
                ModelEligibilityStatus.DISABLED,
                ModelAdmissionReason.MODEL_DISABLED,
            )
        if not request.runtime_supported:
            return decision(
                ModelEligibilityStatus.RUNTIME_INCOMPATIBLE,
                ModelAdmissionReason.RUNTIME_UNSUPPORTED,
            )
        if request.installed and not request.verified:
            return decision(
                ModelEligibilityStatus.VERIFICATION_REQUIRED,
                ModelAdmissionReason.VERIFICATION_REQUIRED,
            )
        if not request.installed:
            return decision(
                ModelEligibilityStatus.DOWNLOAD_REQUIRED
                if request.download_required
                else ModelEligibilityStatus.NOT_INSTALLED,
                ModelAdmissionReason.DOWNLOAD_REQUIRED
                if request.download_required
                else ModelAdmissionReason.MODEL_NOT_INSTALLED,
            )
        if not request.available:
            return decision(
                ModelEligibilityStatus.RUNTIME_INCOMPATIBLE,
                ModelAdmissionReason.MODEL_UNAVAILABLE,
            )
        if request.minimum_gpu_count > len(self.inventory.gpu_vram_bytes):
            return decision(
                ModelEligibilityStatus.HARDWARE_INSUFFICIENT,
                ModelAdmissionReason.MULTI_GPU_REQUIRED,
            )
        if request.minimum_compute_capability is not None:
            minimum = _compute_tuple(request.minimum_compute_capability)
            detected = tuple(
                candidate
                for candidate in (
                    _compute_tuple(value)
                    for value in self.inventory.gpu_compute_capabilities
                )
                if candidate is not None
            )
            detected_capability = (
                min(detected)
                if request.supports_multi_gpu and len(detected) > 1
                else max(detected, default=())
            )
            if minimum is None or not detected or detected_capability < minimum:
                return decision(
                    ModelEligibilityStatus.HARDWARE_INSUFFICIENT,
                    ModelAdmissionReason.COMPUTE_CAPABILITY_UNSUPPORTED,
                )
        if request.required_vram_bytes is None or request.required_ram_bytes is None:
            return decision(
                ModelEligibilityStatus.HARDWARE_INSUFFICIENT,
                ModelAdmissionReason.METADATA_INCOMPLETE,
            )

        admission = self.planner.admit(
            installed=True,
            available=True,
            required_vram_bytes=request.required_vram_bytes,
            minimum_vram_bytes=request.minimum_vram_bytes,
            required_ram_bytes=request.required_ram_bytes,
            offload_required_ram_bytes=request.offload_required_ram_bytes,
            offload_policy=request.offload_policy,
            supports_multi_gpu=request.supports_multi_gpu,
        )
        if admission.status is HardwareAdmissionStatus.RUNNABLE:
            return decision(
                ModelEligibilityStatus.RUNNABLE_NOW,
                ModelAdmissionReason.ELIGIBLE,
                performance=PerformanceClass.INTERACTIVE,
            )
        if admission.status is HardwareAdmissionStatus.OFFLOAD_REQUIRED:
            if request.offload_performance in {
                PerformanceClass.INTERACTIVE,
                PerformanceClass.ACCEPTABLE,
            }:
                return decision(
                    ModelEligibilityStatus.RUNNABLE_WITH_OFFLOAD,
                    ModelAdmissionReason.ELIGIBLE,
                    performance=request.offload_performance,
                )
            return decision(
                ModelEligibilityStatus.FUTURE_CAPABLE,
                ModelAdmissionReason.OFFLOAD_TOO_SLOW,
                performance=request.offload_performance,
            )

        reasons: list[ModelAdmissionReason] = []
        if request.required_ram_bytes > usable_ram:
            reasons.append(ModelAdmissionReason.RAM_INSUFFICIENT)
        aggregate_vram = sum(
            max(0, value - self.planner.gpu_reserve_bytes)
            for value in self.inventory.gpu_vram_bytes
        )
        model_usable_vram = (
            aggregate_vram
            if request.supports_multi_gpu and len(self.inventory.gpu_vram_bytes) > 1
            else usable_vram
        )
        if request.required_vram_bytes > model_usable_vram:
            reasons.append(ModelAdmissionReason.VRAM_INSUFFICIENT)
        if not reasons:
            reasons.append(ModelAdmissionReason.METADATA_INCOMPLETE)
        return decision(ModelEligibilityStatus.HARDWARE_INSUFFICIENT, *reasons)
