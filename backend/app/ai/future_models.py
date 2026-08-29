from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.ai.admission import (
    ModelAdmissionEngine,
    ModelAdmissionRequest,
    PerformanceClass,
)
from app.ai.catalog import (
    ModelAvailability,
    ModelCapability,
    ModelModality,
    ModelScaleClass,
    RuntimeModel,
)
from app.hardware.planner import (
    GIBIBYTE,
    HardwareInventory,
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
    minimum_compute_capability: str | None = None
    offload_performance: PerformanceClass = PerformanceClass.SLOW
    pipeline_parallel: bool = False
    sharding: bool = False
    device_placement: str = "runtime_managed"

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
        if not isinstance(self.offload_performance, PerformanceClass):
            raise TypeError("future model offload performance is invalid")
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

    def as_registry_model(self) -> RuntimeModel:
        """Project planning metadata through the canonical runtime model schema."""

        return RuntimeModel(
            reference=self.profile_id,
            display_name=f"{self.model_family} {self.parameter_class}",
            modality=self.modalities[0],
            family=self.model_family,
            parameter_class=self.parameter_class,
            capabilities=self.capabilities,
            context_window=self.context_window,
            quantization=self.quantization,
            availability=ModelAvailability.UNKNOWN,
            scale_class=self.scale_class,
            required_vram_bytes=self.required_vram_bytes,
            minimum_vram_bytes=self.minimum_vram_bytes,
            required_ram_bytes=self.required_ram_bytes,
            offload_required_ram_bytes=self.offload_required_ram_bytes,
            offload_policy=self.offload_policy,
            offload_performance=self.offload_performance,
            installed=False,
            verified=False,
            runtime_compatible=False,
            supports_multi_gpu=self.tensor_parallel_gpu_count > 1,
        )


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
        pipeline_parallel=True,
        sharding=True,
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
        pipeline_parallel=True,
        sharding=True,
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
        pipeline_parallel=True,
        sharding=True,
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
        pipeline_parallel=True,
        sharding=True,
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
        pipeline_parallel=True,
        sharding=True,
    ),
)


HARDWARE_SIMULATION_TIERS_GIB = (
    12,
    16,
    24,
    32,
    48,
    64,
    80,
    96,
    128,
    256,
    512,
    1_024,
)
HARDWARE_SIMULATION_RAM_GIB = {
    12: 80,
    16: 96,
    24: 128,
    32: 192,
    48: 256,
    64: 384,
    80: 512,
    96: 512,
    128: 768,
    256: 1_024,
    512: 2_048,
    1_024: 4_096,
}


def _simulated_inventory(
    vram_gib: int,
    gpu_count: int,
    ram_gib: int,
) -> HardwareInventory:
    per_gpu = vram_gib * GIBIBYTE
    return HardwareInventory(
        total_ram_bytes=ram_gib * GIBIBYTE,
        gpu_vram_bytes=(per_gpu,) * gpu_count,
        gpu_names=tuple(
            f"Hypothetical {vram_gib} GiB accelerator {index + 1}"
            for index in range(gpu_count)
        ),
        gpu_compute_capabilities=("9.0",) * gpu_count,
        gpu_vendors=("simulation",) * gpu_count,
        cpu_model="Simulated CPU",
        cpu_logical_count=64,
        os_name="simulation",
        os_version="1",
        architecture="x86_64",
    )


def _admissions(inventory: HardwareInventory) -> list[dict[str, object]]:
    engine = ModelAdmissionEngine(inventory)
    admissions: list[dict[str, object]] = []
    for profile in FUTURE_MODEL_CONTRACTS:
        admission = engine.evaluate(
            ModelAdmissionRequest(
                installed=True,
                available=True,
                required_vram_bytes=profile.required_vram_bytes,
                minimum_vram_bytes=profile.minimum_vram_bytes,
                required_ram_bytes=profile.required_ram_bytes,
                offload_required_ram_bytes=profile.offload_required_ram_bytes,
                offload_policy=profile.offload_policy,
                offload_performance=profile.offload_performance,
                supports_multi_gpu=(profile.tensor_parallel_gpu_count > 1),
                minimum_compute_capability=profile.minimum_compute_capability,
            )
        )
        admissions.append(
            {
                "profile_id": profile.profile_id,
                "scale_class": profile.scale_class.value,
                "status": admission.status.value,
                "reasons": [reason.value for reason in admission.reasons],
                "performance": admission.performance.value,
                "offload_policy": profile.offload_policy.value,
                "tensor_parallel_gpu_count": profile.tensor_parallel_gpu_count,
                "pipeline_parallel": profile.pipeline_parallel,
                "sharding": profile.sharding,
            }
        )
    return admissions


def hardware_admission_matrix() -> dict[str, object]:
    tiers: list[dict[str, object]] = []
    for vram_gib in HARDWARE_SIMULATION_TIERS_GIB:
        ram_gib = HARDWARE_SIMULATION_RAM_GIB[vram_gib]
        inventory = _simulated_inventory(vram_gib, 1, ram_gib)
        tiers.append(
            {
                "gpu_vram_gib": vram_gib,
                "system_ram_gib": ram_gib,
                "gpu_count": 1,
                "admissions": _admissions(inventory),
            }
        )
    multi_gpu_layouts = []
    for gpu_count, per_gpu_gib, ram_gib in (
        (2, 24, 256),
        (2, 48, 512),
        (4, 80, 1_024),
        (8, 80, 2_048),
    ):
        multi_gpu_layouts.append(
            {
                "gpu_count": gpu_count,
                "per_gpu_vram_gib": per_gpu_gib,
                "system_ram_gib": ram_gib,
                "admissions": _admissions(
                    _simulated_inventory(per_gpu_gib, gpu_count, ram_gib)
                ),
            }
        )
    return {
        "simulation_only": True,
        "actual_execution_claimed": False,
        "gpu_reserve_gib": 1.5,
        "ram_reserve_gib": 8,
        "ram_assumptions_gib": HARDWARE_SIMULATION_RAM_GIB,
        "tiers": tiers,
        "multi_gpu_layouts": multi_gpu_layouts,
    }
