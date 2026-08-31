import pytest

from app.ai.admission import PerformanceClass
from app.hardware import GIBIBYTE, OffloadPolicy
from app.images import ImageModelStatus, image_model_contract


def test_current_sdxl_is_a_capability_profile_not_a_permanent_special_case():
    current = image_model_contract("sdxl-base-1.0")
    admitted = image_model_contract("flux2-klein-base-4b-fp8")
    future = image_model_contract("flux-family-local")

    assert current.generation and current.editing
    assert current.status is ImageModelStatus.RUNNABLE_NOW
    assert admitted.status is ImageModelStatus.RUNNABLE_NOW
    assert admitted.workflow_adapter == "comfyui-flux2-klein-base"
    assert {artifact.role for artifact in admitted.artifacts} == {
        "diffusion_model",
        "text_encoder",
        "vae",
    }
    assert all(len(artifact.sha256) == 64 for artifact in admitted.artifacts)
    assert admitted.required_vram_bytes == 23 * GIBIBYTE // 2
    assert admitted.minimum_vram_bytes == 21 * GIBIBYTE // 2
    assert admitted.offload_required_ram_bytes == 32 * GIBIBYTE
    assert admitted.offload_policy is OffloadPolicy.CPU
    assert admitted.offload_performance is PerformanceClass.ACCEPTABLE
    assert future.generation and future.editing
    assert future.status is ImageModelStatus.NOT_INSTALLED
    assert future.workflow_adapter != current.workflow_adapter


def test_unknown_image_profile_fails_closed():
    with pytest.raises(ValueError, match="not registered"):
        image_model_contract("untrusted-remote-model")
