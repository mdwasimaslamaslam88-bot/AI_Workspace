from unittest.mock import Mock

import pytest

from app.ai.admission import (
    ModelAdmissionEngine,
    ModelAdmissionReason,
    ModelAdmissionRequest,
    ModelEligibilityStatus,
    PerformanceClass,
)
from app.ai.catalog import (
    ModelCapability,
    ModelCatalog,
    ModelScaleClass,
    RuntimeModel,
)
from app.ai.routing import ModelTask, TaskAwareModelRouter
from app.hardware import GIBIBYTE, HardwareInventory, OffloadPolicy


def _hardware(vram_gib: int, *, gpu_count: int = 1, ram_gib: int = 80):
    return HardwareInventory(
        total_ram_bytes=ram_gib * GIBIBYTE,
        gpu_vram_bytes=(vram_gib * GIBIBYTE,) * gpu_count,
        gpu_names=tuple(f"GPU {index}" for index in range(gpu_count)),
        gpu_compute_capabilities=("9.0",) * gpu_count,
        cpu_model="Test CPU",
        cpu_logical_count=32,
        os_name="Linux",
        os_version="test",
        architecture="x86_64",
    )


def test_admission_reports_exact_fail_closed_reasons():
    engine = ModelAdmissionEngine(_hardware(12))

    unverified = engine.evaluate(
        ModelAdmissionRequest(installed=True, available=True, verified=False)
    )
    oversized = engine.evaluate(
        ModelAdmissionRequest(
            installed=True,
            available=True,
            required_vram_bytes=48 * GIBIBYTE,
            required_ram_bytes=128 * GIBIBYTE,
        )
    )

    assert unverified.status is ModelEligibilityStatus.VERIFICATION_REQUIRED
    assert unverified.reasons == (ModelAdmissionReason.VERIFICATION_REQUIRED,)
    assert oversized.status is ModelEligibilityStatus.HARDWARE_INSUFFICIENT
    assert set(oversized.reasons) == {
        ModelAdmissionReason.VRAM_INSUFFICIENT,
        ModelAdmissionReason.RAM_INSUFFICIENT,
    }


def test_offload_is_admitted_only_when_performance_is_acceptable():
    engine = ModelAdmissionEngine(_hardware(12))
    base = dict(
        installed=True,
        available=True,
        required_vram_bytes=18 * GIBIBYTE,
        minimum_vram_bytes=6 * GIBIBYTE,
        required_ram_bytes=24 * GIBIBYTE,
        offload_required_ram_bytes=40 * GIBIBYTE,
        offload_policy=OffloadPolicy.CPU,
    )

    accepted = engine.evaluate(
        ModelAdmissionRequest(
            **base,
            offload_performance=PerformanceClass.ACCEPTABLE,
        )
    )
    slow = engine.evaluate(
        ModelAdmissionRequest(
            **base,
            offload_performance=PerformanceClass.SLOW,
        )
    )

    assert accepted.status is ModelEligibilityStatus.RUNNABLE_WITH_OFFLOAD
    assert accepted.eligible
    assert slow.status is ModelEligibilityStatus.FUTURE_CAPABLE
    assert slow.reasons == (ModelAdmissionReason.OFFLOAD_TOO_SLOW,)


@pytest.mark.asyncio
async def test_gpu_upgrade_recalculates_admission_routing_and_fallbacks():
    small = RuntimeModel(
        reference="small",
        display_name="Current 8B",
        family="generic",
        scale_class=ModelScaleClass.SEVEN_TO_EIGHT_B,
        capabilities=(ModelCapability.TEXT_GENERATION,),
        required_vram_bytes=6 * GIBIBYTE,
        required_ram_bytes=10 * GIBIBYTE,
    )
    large = RuntimeModel(
        reference="large",
        display_name="Future 70B",
        family="generic",
        scale_class=ModelScaleClass.SEVENTY_B,
        capabilities=(ModelCapability.TEXT_GENERATION,),
        required_vram_bytes=40 * GIBIBYTE,
        required_ram_bytes=48 * GIBIBYTE,
    )

    async def discover_models(*, reference_selector=None):
        values = (small, large)
        return values if reference_selector is None else tuple(
            value for value in values if reference_selector(value.reference)
        )

    runtime = Mock(runtime_id="test", supports_reference_selector=True)
    runtime.discover_models = discover_models
    current_models = await ModelCatalog(
        (runtime,), hardware_inventory=_hardware(12)
    ).list_models()
    future_models = await ModelCatalog(
        (runtime,), hardware_inventory=_hardware(48)
    ).list_models()
    router = TaskAwareModelRouter()

    current = router.select(current_models, ModelTask.REASONING)
    future = router.select(future_models, ModelTask.REASONING)
    large_descriptor = next(
        model for model in current_models if model.display_name == "Future 70B"
    )

    assert current.model_id != future.model_id
    assert large_descriptor.eligibility_status is ModelEligibilityStatus.HARDWARE_INSUFFICIENT
    assert large_descriptor.fallback_model_id == current.model_id
    assert future.model_id in {
        model.model_id
        for model in future_models
        if model.display_name == "Future 70B"
    }
