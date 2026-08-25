from unittest.mock import AsyncMock, Mock

import pytest

from app.ai.catalog import (
    ModelAvailability,
    ModelCapability,
    ModelCatalog,
    RuntimeModel,
)
from app.hardware.planner import (
    GIBIBYTE,
    HardwareClass,
    HardwareInventory,
    HardwareAdmissionStatus,
    HardwarePlanner,
    OffloadPolicy,
    detect_hardware,
)


def test_hardware_inventory_classifies_current_and_future_gpu_layouts():
    assert HardwareInventory(16 * GIBIBYTE).hardware_class is HardwareClass.CPU_ONLY
    assert (
        HardwareInventory(
            64 * GIBIBYTE,
            (12 * GIBIBYTE,),
        ).hardware_class
        is HardwareClass.GPU_8_TO_15GB
    )
    assert (
        HardwareInventory(
            256 * GIBIBYTE,
            (80 * GIBIBYTE, 80 * GIBIBYTE),
        ).hardware_class
        is HardwareClass.MULTI_GPU
    )


def test_hardware_planner_reserves_capacity_and_fails_closed_on_unknowns():
    planner = HardwarePlanner(
        HardwareInventory(64 * GIBIBYTE, (12 * GIBIBYTE,)),
    )

    assert planner.runnable_now(
        installed=True,
        required_vram_bytes=9 * GIBIBYTE,
        required_ram_bytes=12 * GIBIBYTE,
    )
    assert not planner.runnable_now(
        installed=True,
        required_vram_bytes=11 * GIBIBYTE,
        required_ram_bytes=12 * GIBIBYTE,
    )
    assert not planner.runnable_now(
        installed=True,
        required_vram_bytes=None,
        required_ram_bytes=12 * GIBIBYTE,
    )
    assert not planner.runnable_now(
        installed=False,
        required_vram_bytes=1 * GIBIBYTE,
        required_ram_bytes=2 * GIBIBYTE,
    )

    multi_gpu_planner = HardwarePlanner(
        HardwareInventory(128 * GIBIBYTE, (24 * GIBIBYTE, 24 * GIBIBYTE)),
    )
    assert multi_gpu_planner.runnable_now(
        installed=True,
        required_vram_bytes=32 * GIBIBYTE,
        required_ram_bytes=48 * GIBIBYTE,
        supports_multi_gpu=True,
    )
    assert not multi_gpu_planner.runnable_now(
        installed=True,
        required_vram_bytes=32 * GIBIBYTE,
        required_ram_bytes=48 * GIBIBYTE,
        supports_multi_gpu=False,
    )


def test_hardware_planner_admits_cpu_compatible_model_without_gpu_vram():
    planner = HardwarePlanner(HardwareInventory(total_ram_bytes=32 * GIBIBYTE))

    assert planner.runnable_now(
        installed=True,
        required_vram_bytes=0,
        required_ram_bytes=512 * 1024**2,
    )
    assert planner.required_hardware_class(0) is HardwareClass.CPU_ONLY


def test_detect_hardware_uses_bounded_model_capacity_and_compute(monkeypatch):
    completed = Mock(
        stdout="NVIDIA GeForce RTX 3060, 12288, 8.6\n",
        returncode=0,
    )
    run = Mock(return_value=completed)
    monkeypatch.setattr("app.hardware.planner.subprocess.run", run)
    monkeypatch.setattr("app.hardware.planner._total_ram_bytes", lambda: 80 * GIBIBYTE)

    inventory = detect_hardware()

    assert inventory == HardwareInventory(
        80 * GIBIBYTE,
        (12 * GIBIBYTE,),
        ("NVIDIA GeForce RTX 3060",),
        ("8.6",),
    )
    command = run.call_args.args[0]
    assert command == [
        "nvidia-smi",
        "--query-gpu=name,memory.total,compute_cap",
        "--format=csv,noheader,nounits",
    ]


def test_hardware_planner_reports_offload_and_insufficient_capacity():
    planner = HardwarePlanner(
        HardwareInventory(80 * GIBIBYTE, (12 * GIBIBYTE,)),
    )

    offload = planner.admit(
        installed=True,
        available=True,
        required_vram_bytes=18 * GIBIBYTE,
        minimum_vram_bytes=6 * GIBIBYTE,
        required_ram_bytes=32 * GIBIBYTE,
        offload_policy=OffloadPolicy.CPU,
    )
    insufficient = planner.admit(
        installed=True,
        available=True,
        required_vram_bytes=136 * GIBIBYTE,
        minimum_vram_bytes=48 * GIBIBYTE,
        required_ram_bytes=250 * GIBIBYTE,
        offload_policy=OffloadPolicy.CPU_OR_TENSOR_PARALLEL,
        supports_multi_gpu=True,
    )

    assert offload.status is HardwareAdmissionStatus.OFFLOAD_REQUIRED
    assert not offload.runnable_now
    assert insufficient.status is HardwareAdmissionStatus.INSUFFICIENT_HARDWARE


@pytest.mark.asyncio
async def test_catalog_marks_oversized_model_unrunnable_and_supplies_fallback():
    small = RuntimeModel(
        reference="small-local",
        display_name="Small local model",
        capabilities=(ModelCapability.TEXT_GENERATION,),
        required_vram_bytes=6 * GIBIBYTE,
        required_ram_bytes=10 * GIBIBYTE,
    )
    oversized = RuntimeModel(
        reference="oversized-local",
        display_name="Oversized local model",
        capabilities=(ModelCapability.TEXT_GENERATION,),
        required_vram_bytes=24 * GIBIBYTE,
        required_ram_bytes=40 * GIBIBYTE,
    )

    async def discover_models(*, reference_selector=None):
        models = (small, oversized)
        if reference_selector is None:
            return models
        return tuple(model for model in models if reference_selector(model.reference))

    runtime = Mock(runtime_id="local-runtime", supports_reference_selector=True)
    runtime.discover_models = discover_models
    catalog = ModelCatalog(
        (runtime,),
        hardware_inventory=HardwareInventory(
            64 * GIBIBYTE,
            (12 * GIBIBYTE,),
        ),
    )

    models = await catalog.list_models()
    by_name = {model.display_name: model for model in models}
    assert by_name["Small local model"].runnable_now
    assert not by_name["Small local model"].future_capable
    assert not by_name["Oversized local model"].runnable_now
    assert by_name["Oversized local model"].future_capable
    assert (
        by_name["Oversized local model"].fallback_model_id
        == by_name["Small local model"].model_id
    )
    assert (
        by_name["Oversized local model"].hardware_class
        is HardwareClass.GPU_24_TO_47GB
    )


@pytest.mark.asyncio
async def test_catalog_never_marks_an_unavailable_model_runnable():
    unavailable = RuntimeModel(
        reference="temporarily-unavailable",
        display_name="Unavailable local model",
        capabilities=(ModelCapability.TEXT_GENERATION,),
        availability=ModelAvailability.UNAVAILABLE,
        required_vram_bytes=1 * GIBIBYTE,
        required_ram_bytes=2 * GIBIBYTE,
    )
    runtime = Mock(runtime_id="local-runtime", supports_reference_selector=True)
    runtime.discover_models = AsyncMock(return_value=(unavailable,))
    catalog = ModelCatalog(
        (runtime,),
        hardware_inventory=HardwareInventory(
            64 * GIBIBYTE,
            (12 * GIBIBYTE,),
        ),
    )

    (model,) = await catalog.list_models()

    assert model.availability is ModelAvailability.UNAVAILABLE
    assert model.runnable_now is False
