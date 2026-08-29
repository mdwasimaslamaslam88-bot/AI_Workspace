from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.hardware.planner import GIBIBYTE


class ImageModelStatus(StrEnum):
    RUNNABLE_NOW = "runnable_now"
    VERIFICATION_REQUIRED = "verification_required"
    NOT_INSTALLED = "not_installed"
    FUTURE_CAPABLE = "future_capable"


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
