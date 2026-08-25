from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.ai.catalog import ModelCapability, ModelModality, ModelScaleClass
from app.hardware.planner import (
    GIBIBYTE,
    HardwareInventory,
    HardwarePlanner,
    OffloadPolicy,
)


class ModelArchitecture(StrEnum):
    DENSE = "dense"
    MIXTURE_OF_EXPERTS = "mixture_of_experts"


@dataclass(frozen=True, slots=True)
class FutureModelContract:
    profile_id: str
    model_family: str
    architecture: ModelArchitecture
    parameter_class: str
    active_parameter_class: str | None
    scale_class: ModelScaleClass
    quantization: str
    runtime: str
    required_vram_bytes: int
    minimum_vram_bytes: int
    required_ram_bytes: int
    offload_policy: OffloadPolicy
    tensor_parallel_gpu_count: int
    context_window: int
    modalities: tuple[ModelModality, ...]
    capabilities: tuple[ModelCapability, ...]
    fallback_role: str
    offload_required_ram_bytes: int | None = None

    def __post_init__(self) -> None:
        if not self.profile_id or self.profile_id != self.profile_id.strip():
            raise ValueError("future model profile_id must be nonblank")
        if not self.model_family or self.model_family != self.model_family.strip():
            raise ValueError("future model family must be nonblank")
        if not isinstance(self.architecture, ModelArchitecture):
            raise TypeError("future model architecture is invalid")
        if not isinstance(self.scale_class, ModelScaleClass):
            raise TypeError("future model scale class is invalid")
        if not isinstance(self.offload_policy, OffloadPolicy):
            raise TypeError("future model offload policy is invalid")
        for field_name, value in (
            ("required_vram_bytes", self.required_vram_bytes),
            ("minimum_vram_bytes", self.minimum_vram_bytes),
            ("required_ram_bytes", self.required_ram_bytes),
            ("tensor_parallel_gpu_count", self.tensor_parallel_gpu_count),
            ("context_window", self.context_window),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"future model {field_name} must be positive")
        if self.offload_required_ram_bytes is not None and (
            isinstance(self.offload_required_ram_bytes, bool)
            or not isinstance(self.offload_required_ram_bytes, int)
            or self.offload_required_ram_bytes < self.required_ram_bytes
        ):
            raise ValueError(
                "future model offload RAM must be at least the host RAM requirement"
            )
        if self.minimum_vram_bytes > self.required_vram_bytes:
            raise ValueError("minimum VRAM cannot exceed required VRAM")
        if not self.modalities or len(set(self.modalities)) != len(self.modalities):
            raise ValueError("future model modalities must be nonempty and unique")
        if not self.capabilities or len(set(self.capabilities)) != len(
            self.capabilities
        ):
            raise ValueError("future model capabilities must be nonempty and unique")


TEXT_CAPABILITIES = (
    ModelCapability.TEXT_GENERATION,
    ModelCapability.CHAT,
    ModelCapability.CODE,
    ModelCapability.STRUCTURED_OUTPUT,
    ModelCapability.TOOL_CALLING,
)


FUTURE_MODEL_CONTRACTS = (
    FutureModelContract(
        "dense-8b-q4",
        "generic-dense",
        ModelArchitecture.DENSE,
        "8B",
        None,
        ModelScaleClass.SEVEN_TO_EIGHT_B,
        "Q4_K_M",
        "ollama-compatible",
        7 * GIBIBYTE,
        4 * GIBIBYTE,
        10 * GIBIBYTE,
        OffloadPolicy.CPU,
        1,
        32_768,
        (ModelModality.TEXT,),
        TEXT_CAPABILITIES,
        "general_chat",
    ),
    FutureModelContract(
        "dense-14b-q4",
        "generic-dense",
        ModelArchitecture.DENSE,
        "14B",
        None,
        ModelScaleClass.FOURTEEN_B,
        "Q4_K_M",
        "ollama-compatible",
        12 * GIBIBYTE,
        6 * GIBIBYTE,
        18 * GIBIBYTE,
        OffloadPolicy.CPU,
        1,
        64_000,
        (ModelModality.TEXT,),
        TEXT_CAPABILITIES,
        "general_chat",
    ),
    FutureModelContract(
        "dense-32b-q4",
        "generic-dense",
        ModelArchitecture.DENSE,
        "32B",
        None,
        ModelScaleClass.THIRTY_TO_THIRTY_FOUR_B,
        "Q4_K_M",
        "ollama-compatible",
        24 * GIBIBYTE,
        10 * GIBIBYTE,
        24 * GIBIBYTE,
        OffloadPolicy.CPU_OR_TENSOR_PARALLEL,
        1,
        128_000,
        (ModelModality.TEXT,),
        TEXT_CAPABILITIES,
        "reasoning",
        offload_required_ram_bytes=40 * GIBIBYTE,
    ),
    FutureModelContract(
        "dense-70b-q4",
        "generic-dense",
        ModelArchitecture.DENSE,
        "70B",
        None,
        ModelScaleClass.SEVENTY_B,
        "Q4_K_M",
        "distributed-local",
        48 * GIBIBYTE,
        16 * GIBIBYTE,
        32 * GIBIBYTE,
        OffloadPolicy.CPU_OR_TENSOR_PARALLEL,
        1,
        128_000,
        (ModelModality.TEXT,),
        TEXT_CAPABILITIES,
        "reasoning",
        offload_required_ram_bytes=88 * GIBIBYTE,
    ),
    FutureModelContract(
        "dense-120b-q4",
        "generic-dense",
        ModelArchitecture.DENSE,
        "120B",
        None,
        ModelScaleClass.HUNDRED_B_PLUS,
        "Q4_K_M",
        "distributed-local",
        80 * GIBIBYTE,
        24 * GIBIBYTE,
        48 * GIBIBYTE,
        OffloadPolicy.CPU_OR_TENSOR_PARALLEL,
        1,
        256_000,
        (ModelModality.TEXT,),
        TEXT_CAPABILITIES,
        "reasoning",
        offload_required_ram_bytes=150 * GIBIBYTE,
    ),
    FutureModelContract(
        "dense-200b-q4",
        "generic-dense",
        ModelArchitecture.DENSE,
        "200B",
        None,
        ModelScaleClass.TWO_HUNDRED_B_PLUS,
        "Q4_K_M",
        "distributed-local",
        136 * GIBIBYTE,
        48 * GIBIBYTE,
        64 * GIBIBYTE,
        OffloadPolicy.CPU_OR_TENSOR_PARALLEL,
        2,
        256_000,
        (ModelModality.TEXT,),
        TEXT_CAPABILITIES,
        "reasoning",
        offload_required_ram_bytes=250 * GIBIBYTE,
    ),
    FutureModelContract(
        "dense-500b-q4",
        "generic-dense",
        ModelArchitecture.DENSE,
        "500B",
        None,
        ModelScaleClass.FIVE_HUNDRED_B_PLUS,
        "Q4_K_M",
        "distributed-local",
        336 * GIBIBYTE,
        96 * GIBIBYTE,
        64 * GIBIBYTE,
        OffloadPolicy.CPU_OR_TENSOR_PARALLEL,
        4,
        512_000,
        (ModelModality.TEXT,),
        TEXT_CAPABILITIES,
        "reasoning",
        offload_required_ram_bytes=625 * GIBIBYTE,
    ),
    FutureModelContract(
        "dense-1000b-q4",
        "generic-dense",
        ModelArchitecture.DENSE,
        "1000B",
        None,
        ModelScaleClass.ONE_THOUSAND_B_PLUS,
        "Q4_K_M",
        "distributed-local",
        672 * GIBIBYTE,
        192 * GIBIBYTE,
        64 * GIBIBYTE,
        OffloadPolicy.CPU_OR_TENSOR_PARALLEL,
        8,
        1_000_000,
        (ModelModality.TEXT,),
        TEXT_CAPABILITIES,
        "reasoning",
        offload_required_ram_bytes=1_250 * GIBIBYTE,
    ),
    FutureModelContract(
        "dense-2000b-q4",
        "generic-dense",
        ModelArchitecture.DENSE,
        "2000B",
        None,
        ModelScaleClass.TWO_THOUSAND_B,
        "Q4_K_M",
        "distributed-local",
        1_344 * GIBIBYTE,
        384 * GIBIBYTE,
        64 * GIBIBYTE,
        OffloadPolicy.CPU_OR_TENSOR_PARALLEL,
        16,
        1_000_000,
        (ModelModality.TEXT,),
        TEXT_CAPABILITIES,
        "reasoning",
        offload_required_ram_bytes=2_500 * GIBIBYTE,
    ),
    FutureModelContract(
        "moe-frontier-q4",
        "generic-moe-frontier",
        ModelArchitecture.MIXTURE_OF_EXPERTS,
        "600B total",
        "60B active",
        ModelScaleClass.MOE_VERY_LARGE,
        "Q4_K_M",
        "distributed-local",
        400 * GIBIBYTE,
        80 * GIBIBYTE,
        64 * GIBIBYTE,
        OffloadPolicy.CPU_OR_TENSOR_PARALLEL,
        4,
        1_000_000,
        (ModelModality.TEXT, ModelModality.MULTIMODAL),
        TEXT_CAPABILITIES + (ModelCapability.VISION_INPUT,),
        "reasoning",
        offload_required_ram_bytes=750 * GIBIBYTE,
    ),
)


HARDWARE_SIMULATION_TIERS_GIB = (12, 16, 24, 48, 80, 96, 128, 256, 512, 1_024)
HARDWARE_SIMULATION_RAM_GIB = {
    12: 80,
    16: 96,
    24: 128,
    48: 256,
    80: 512,
    96: 512,
    128: 768,
    256: 1_024,
    512: 2_048,
    1_024: 4_096,
}


def hardware_admission_matrix() -> dict[str, object]:
    tiers: list[dict[str, object]] = []
    for vram_gib in HARDWARE_SIMULATION_TIERS_GIB:
        ram_gib = HARDWARE_SIMULATION_RAM_GIB[vram_gib]
        planner = HardwarePlanner(
            HardwareInventory(
                total_ram_bytes=ram_gib * GIBIBYTE,
                gpu_vram_bytes=(vram_gib * GIBIBYTE,),
                gpu_names=(f"Hypothetical {vram_gib} GiB accelerator",),
            )
        )
        admissions = []
        for profile in FUTURE_MODEL_CONTRACTS:
            admission = planner.admit(
                installed=True,
                available=True,
                required_vram_bytes=profile.required_vram_bytes,
                minimum_vram_bytes=profile.minimum_vram_bytes,
                required_ram_bytes=profile.required_ram_bytes,
                offload_required_ram_bytes=(
                    profile.offload_required_ram_bytes
                ),
                offload_policy=profile.offload_policy,
                supports_multi_gpu=(profile.tensor_parallel_gpu_count > 1),
            )
            admissions.append(
                {
                    "profile_id": profile.profile_id,
                    "scale_class": profile.scale_class.value,
                    "status": admission.status.value,
                    "offload_policy": profile.offload_policy.value,
                    "tensor_parallel_gpu_count": (
                        profile.tensor_parallel_gpu_count
                    ),
                }
            )
        tiers.append(
            {
                "gpu_vram_gib": vram_gib,
                "system_ram_gib": ram_gib,
                "admissions": admissions,
            }
        )
    return {
        "simulation_only": True,
        "actual_execution_claimed": False,
        "gpu_reserve_gib": 1.5,
        "ram_reserve_gib": 8,
        "ram_assumptions_gib": HARDWARE_SIMULATION_RAM_GIB,
        "tiers": tiers,
    }
