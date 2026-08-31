from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.ai.admission import PerformanceClass
from app.hardware.planner import GIBIBYTE, OffloadPolicy


class ImageModelStatus(StrEnum):
    RUNNABLE_NOW = "runnable_now"
    VERIFICATION_REQUIRED = "verification_required"
    NOT_INSTALLED = "not_installed"
    FUTURE_CAPABLE = "future_capable"


@dataclass(frozen=True, slots=True)
class ImageModelArtifact:
    role: str
    filename: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ImageModelContract:
    profile_id: str
    display_name: str
    family: str
    parameter_class: str
    runtime: str
    workflow_adapter: str
    precision: str
    maximum_resolution: int
    required_vram_bytes: int
    required_ram_bytes: int
    generation: bool
    editing: bool
    installed: bool
    verified: bool
    adapter_supported: bool
    minimum_vram_bytes: int | None = None
    offload_required_ram_bytes: int | None = None
    offload_policy: OffloadPolicy = OffloadPolicy.NONE
    offload_performance: PerformanceClass = PerformanceClass.UNSUPPORTED
    artifacts: tuple[ImageModelArtifact, ...] = ()

    @property
    def status(self) -> ImageModelStatus:
        if not self.installed:
            return ImageModelStatus.NOT_INSTALLED
        if not self.verified:
            return ImageModelStatus.VERIFICATION_REQUIRED
        if self.adapter_supported:
            return ImageModelStatus.RUNNABLE_NOW
        return ImageModelStatus.FUTURE_CAPABLE


IMAGE_MODEL_CONTRACTS = (
    ImageModelContract(
        profile_id="sdxl-base-1.0",
        display_name="Stable Diffusion XL Base 1.0",
        family="Stable Diffusion XL",
        parameter_class="3.5B",
        runtime="comfyui",
        workflow_adapter="comfyui-sdxl",
        precision="FP16",
        maximum_resolution=1_024,
        required_vram_bytes=9 * GIBIBYTE,
        required_ram_bytes=16 * GIBIBYTE,
        generation=True,
        editing=True,
        installed=True,
        verified=True,
        adapter_supported=True,
    ),
    ImageModelContract(
        profile_id="flux2-klein-base-4b-fp8",
        display_name="FLUX.2 Klein Base 4B FP8",
        family="FLUX.2 Klein",
        parameter_class="4B",
        runtime="comfyui",
        workflow_adapter="comfyui-flux2-klein-base",
        precision="FP8",
        maximum_resolution=1_024,
        required_vram_bytes=23 * GIBIBYTE // 2,
        required_ram_bytes=32 * GIBIBYTE,
        generation=True,
        editing=True,
        installed=True,
        verified=True,
        adapter_supported=True,
        minimum_vram_bytes=21 * GIBIBYTE // 2,
        offload_required_ram_bytes=32 * GIBIBYTE,
        offload_policy=OffloadPolicy.CPU,
        offload_performance=PerformanceClass.ACCEPTABLE,
        artifacts=(
            ImageModelArtifact(
                role="diffusion_model",
                filename="flux-2-klein-base-4b-fp8.safetensors",
                size_bytes=4_089_498_488,
                sha256=(
                    "44bab3a86fe98b85d21dd2a4729ebdc3"
                    "ae51fb8a39f76e457e18c724219e6840"
                ),
            ),
            ImageModelArtifact(
                role="text_encoder",
                filename="qwen_3_4b.safetensors",
                size_bytes=8_044_982_048,
                sha256=(
                    "6c671498573ac2f7a5501502ccce8d2b"
                    "08ea6ca2f661c458e708f36b36edfc5a"
                ),
            ),
            ImageModelArtifact(
                role="vae",
                filename="flux2-vae.safetensors",
                size_bytes=336_213_556,
                sha256=(
                    "d64f3a68e1cc4f9f4e29b6e0da38a02"
                    "04fe9a49f2d4053f0ec1fa1ca02f9c4b5"
                ),
            ),
        ),
    ),
    ImageModelContract(
        profile_id="sdxl-lightning-lora",
        display_name="SDXL Lightning LoRA",
        family="Stable Diffusion XL Lightning",
        parameter_class="3.5B",
        runtime="comfyui",
        workflow_adapter="comfyui-sdxl-lightning",
        precision="FP16",
        maximum_resolution=1_024,
        required_vram_bytes=9 * GIBIBYTE,
        required_ram_bytes=16 * GIBIBYTE,
        generation=True,
        editing=True,
        installed=False,
        verified=False,
        adapter_supported=False,
    ),
    ImageModelContract(
        profile_id="flux-family-local",
        display_name="Future FLUX-family local model",
        family="FLUX",
        parameter_class="runtime supplied",
        runtime="future-local-image-runtime",
        workflow_adapter="future-flux-adapter",
        precision="runtime supplied",
        maximum_resolution=2_048,
        required_vram_bytes=16 * GIBIBYTE,
        required_ram_bytes=32 * GIBIBYTE,
        generation=True,
        editing=True,
        installed=False,
        verified=False,
        adapter_supported=False,
    ),
)


def image_model_contract(profile_id: str) -> ImageModelContract:
    if not isinstance(profile_id, str) or not profile_id:
        raise ValueError("image model profile must be nonblank")
    for contract in IMAGE_MODEL_CONTRACTS:
        if contract.profile_id == profile_id:
            return contract
    raise ValueError("image model profile is not registered")
