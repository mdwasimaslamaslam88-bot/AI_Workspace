from unittest.mock import Mock

import pytest

from app.ai.catalog import (
    ModelCapability,
    ModelCatalog,
    RuntimeModel,
)
from app.hardware.planner import (
    GIBIBYTE,
    HardwareClass,
    HardwareInventory,
    HardwarePlanner,
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


def test_detect_hardware_uses_capacity_only(monkeypatch):
    completed = Mock(stdout="12288\n", returncode=0)
    run = Mock(return_value=completed)
    monkeypatch.setattr("app.hardware.planner.subprocess.run", run)
    monkeypatch.setattr("app.hardware.planner._total_ram_bytes", lambda: 80 * GIBIBYTE)

    inventory = detect_hardware()

    assert inventory == HardwareInventory(80 * GIBIBYTE, (12 * GIBIBYTE,))
    command = run.call_args.args[0]
    assert command == [
        "nvidia-smi",
        "--query-gpu=memory.total",
        "--format=csv,noheader,nounits",
    ]


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
    assert not by_name["Oversized local model"].runnable_now
    assert (
        by_name["Oversized local model"].fallback_model_id
        == by_name["Small local model"].model_id
    )
    assert (
        by_name["Oversized local model"].hardware_class
        is HardwareClass.GPU_24_TO_47GB
    )
